from __future__ import annotations
import json
import logging
import re
from typing import Any, Optional
from fastapi import APIRouter, Query, HTTPException, Response, Depends
from pydantic import BaseModel

from db.database import Database
from backend.deps import get_database
from backend.constants import VALID_CATEGORIES, VALID_SOURCES

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


class PartItem(BaseModel):
    id: int
    source: str
    name: str
    category: str
    url: str
    thumbnail_url: Optional[str]
    price_pkr: Optional[int]
    specs: Optional[dict[str, Any]] = None


class PartsResponse(BaseModel):
    items: list[PartItem]
    total: int


class SourceHealth(BaseModel):
    stale: bool
    last_run_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_products: Optional[int] = None
    before_active: Optional[int] = None
    after_active: Optional[int] = None
    last_swept: Optional[int] = None
    last_error: Optional[str] = None


class StatsResponse(BaseModel):
    total_parts: int
    total_price_rows: int
    by_source: dict[str, int]
    by_category: dict[str, int]
    sources: dict[str, SourceHealth]


class SearchIndexResponse(BaseModel):
    srcs: list[str]
    rows: list[list]
    version: str


# ---------------------------------------------------------------------------
# _safe_error: deny-by-default classifier (fix round 2).
#
# Round 1 was still architecturally a strip-list ("redact everything that
# looks like a secret/path, keep the rest") — and a strip-list is always one
# pattern behind. Both of the round-2 review's breaks exploited the same
# structural gap: `_LONG_TOKEN_RE`'s char class excluded `.` and `%`, so a
# secret or path chopped into short runs by dots (a hostname) or
# percent-escapes (an encoded path) sailed through every pattern untouched;
# a unicode homoglyph would evade the ASCII-only path/SQL patterns the same
# way. No amount of pattern-adding closes that class of gap, because the
# function was still built to pass input through by default and only
# subtract known-bad shapes from it.
#
# This version never passes input through. It classifies the leading
# exception-type name (or a known literal phrase) against
# `_KNOWN_ERROR_PREFIXES` below and returns ONLY the matched constant
# string — never a slice, never a substitution, never any byte of the
# original. The `error` text stored in scrape_runs and surfaced through
# `source_health()` into `/api/stats` is produced entirely by this repo's
# own scrapers (scrapers/exceptions.py, scrapers/base_scraper.py,
# run_all.py, scrapers/prebuilts/run_prebuilts.py), so its useful shapes are
# a small, known set: `ScrapeIncomplete: ...`, `HostBlocked: ...`,
# `RuntimeError: Failed to fetch ...: HTTP 403`, `TimeoutError: ...`,
# `returned 0 products`/`returned 0 prebuilts`, `host blocked mid-run: ...`.
# Anything that doesn't match one of those shapes — however it's
# structured, ASCII or not — falls straight through to
# `_SAFE_ERROR_FALLBACK`.
_KNOWN_ERROR_PREFIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^HostBlocked\b"),
     "Site blocked automated requests during this run"),
    (re.compile(r"^host blocked mid-run\b"),
     "Site blocked automated requests during this run"),
    (re.compile(r"^ScrapeIncomplete\b"),
     "Scrape did not finish — some pages could not be read"),
    (re.compile(r"^TimeoutError\b"),
     "Site did not respond in time"),
    (re.compile(r"^returned 0 (?:products|prebuilts)$"),
     "Scraper completed but found no listings"),
)
# RuntimeError is scrapers/base_scraper.py's fetch()-failure type. Some of
# those carry an HTTP status worth surfacing on the STALE ribbon (403 vs. a
# generic failure is a genuinely different diagnosis) — allowed only as a
# narrow field extracted with a strict pattern and re-emitted into OUR OWN
# template below, never copied from the input.
_RUNTIME_ERROR_RE = re.compile(r"^RuntimeError\b")
_HTTP_STATUS_RE = re.compile(r"\b([45]\d{2})\b")
_RUNTIME_ERROR_FALLBACK = "Repeated fetch failures against this site"

_SAFE_ERROR_FALLBACK = "An internal error occurred"
# Backstop only — every branch above already returns a short hand-written
# constant, so this can never actually bite. Kept in case a phrase is ever
# lengthened without re-checking this file.
_SAFE_ERROR_MAX_LEN = 120

# Every string _safe_error can return that is NOT the templated HTTP-status
# phrase — exported for tests to assert output is drawn only from this
# allowlist (plus `_HTTP_STATUS_PHRASE_RE` for the one templated case).
_FIXED_SAFE_ERROR_MESSAGES: frozenset[str] = frozenset(
    {phrase for _, phrase in _KNOWN_ERROR_PREFIXES}
    | {_RUNTIME_ERROR_FALLBACK, _SAFE_ERROR_FALLBACK}
)
_HTTP_STATUS_PHRASE_RE = re.compile(r"^Fetch failed — site returned HTTP [45]\d{2}$")


def _safe_error(error: str | None) -> str | None:
    """
    Deny-by-default classifier for the `error` text stored in scrape_runs
    and surfaced through source_health() into /api/stats, which drives the
    landing page's STALE ribbon reason. /api/stats is public.

    A reader needs to know *which class of failure* happened, not the free
    text, so this matches the leading exception-type name (or a known
    literal phrase, see `_KNOWN_ERROR_PREFIXES`) and returns the mapped
    constant string — or, for RuntimeError, a template filled with an HTTP
    status code that was itself validated against `_HTTP_STATUS_RE` before
    being re-emitted. No byte of the original input is ever part of the
    response. Anything unrecognised — including inputs specifically crafted
    to evade a pattern list — falls through to `_SAFE_ERROR_FALLBACK`.

    The full, untruncated text is (a) logged server-side via `logger.error`
    right here, and (b) written verbatim to the scrape_runs row by
    `Database.record_scrape_run`. This function only guards what leaves the
    `/api/stats` response boundary — storage and logs are unaffected.

    `None` means "no error" (the DB column was NULL / the run succeeded)
    and is passed through as `None`. Any other falsy-after-strip value — an
    empty string, an exception whose str() was empty, a whitespace-only
    message — still means an error happened, so it maps to the generic
    fallback rather than silently vanishing.
    """
    if error is None:
        return None

    stripped = error.strip()
    if not stripped:
        logger.error("scrape run failed with an empty error message")
        return _SAFE_ERROR_FALLBACK

    logger.error("scrape run error (full detail, pre-redaction): %s", stripped)

    first = stripped.splitlines()[0].strip()
    if not first:
        return _SAFE_ERROR_FALLBACK[:_SAFE_ERROR_MAX_LEN]

    for pattern, phrase in _KNOWN_ERROR_PREFIXES:
        if pattern.search(first):
            return phrase[:_SAFE_ERROR_MAX_LEN]

    if _RUNTIME_ERROR_RE.search(first):
        status_match = _HTTP_STATUS_RE.search(first)
        if status_match:
            return f"Fetch failed — site returned HTTP {status_match.group(1)}"[:_SAFE_ERROR_MAX_LEN]
        return _RUNTIME_ERROR_FALLBACK[:_SAFE_ERROR_MAX_LEN]

    return _SAFE_ERROR_FALLBACK[:_SAFE_ERROR_MAX_LEN]


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Database = Depends(get_database)):
    data = db.stats()
    for health in data["sources"].values():
        health["last_error"] = _safe_error(health.get("last_error"))
    return data


@router.get("/parts/filters", response_model=dict[str, list[str]])
def get_filter_options(category: str = Query(...), db: Database = Depends(get_database)):
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category '{category}'")
    return db.get_filter_options(category)


@router.get("/search-index", response_model=SearchIndexResponse)
def get_search_index(response: Response, category: str = Query(...), db: Database = Depends(get_database)):
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category '{category}'")
    payload = db.search_index(category)
    # Cloudflare fronts Render, so repeat hits are served from edge cache and
    # the client revalidates cheaply with If-None-Match.
    response.headers["ETag"] = f'"{payload["version"]}"'
    response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
    return payload


@router.get("/parts", response_model=PartsResponse)
def get_parts(
    category:    Optional[str] = Query(None),
    source:      Optional[str] = Query(None),
    min_price:   Optional[int] = Query(None, ge=0),
    max_price:   Optional[int] = Query(None, ge=0),
    sort:        str           = Query("price_asc", pattern="^price_(asc|desc)$"),
    limit:       int           = Query(50, ge=1, le=100),
    offset:      int           = Query(0, ge=0),
    brand:       Optional[str] = Query(None),
    socket:      Optional[str] = Query(None),
    model:       Optional[str] = Query(None),
    vram:        Optional[str] = Query(None),
    ddr_type:    Optional[str] = Query(None),
    speed:       Optional[str] = Query(None),
    chipset:     Optional[str] = Query(None),
    wattage:     Optional[str] = Query(None),
    rating:      Optional[str] = Query(None),
    form_factor: Optional[str] = Query(None),
    cooling_type: Optional[str] = Query(None, alias="type"),
    aio_size:    Optional[str] = Query(None),
    fan_size:    Optional[str] = Query(None),
    interface:   Optional[str] = Query(None),
    capacity:    Optional[str] = Query(None),
    q:           Optional[str] = Query(None),
    ids:         Optional[str] = Query(None, description="Comma-separated part IDs, max 50"),
    db:          Database      = Depends(get_database),
):
    if category and category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category '{category}'. Valid: {sorted(VALID_CATEGORIES)}")
    if source and source not in VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"Unknown source '{source}'")

    id_list: Optional[list[int]] = None
    if ids is not None:
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="ids must be comma-separated integers")
        if len(id_list) > 50:
            raise HTTPException(status_code=422, detail="ids accepts at most 50 values")

    raw_spec_filters = {
        "brand": brand, "socket": socket, "model": model, "vram": vram,
        "ddr_type": ddr_type, "speed": speed, "chipset": chipset,
        "wattage": wattage, "rating": rating, "form_factor": form_factor,
        "type": cooling_type, "aio_size": aio_size, "fan_size": fan_size,
        "interface": interface, "capacity": capacity,
    }
    specs_filter = {k: v for k, v in raw_spec_filters.items() if v is not None} or None

    items, total = db.list_parts(
        category=category,
        source=source,
        min_price=min_price,
        max_price=max_price,
        specs_filter=specs_filter,
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
        ids=id_list,
    )

    parsed_items = []
    for item in items:
        specs_raw = item.pop("specs", None)
        item["specs"] = json.loads(specs_raw) if specs_raw else None
        parsed_items.append(item)

    return {"items": parsed_items, "total": total}
