from __future__ import annotations
import json
import re
from typing import Any, Optional
from fastapi import APIRouter, Query, HTTPException, Response, Depends
from pydantic import BaseModel

from db.database import Database
from backend.deps import get_database
from backend.constants import VALID_CATEGORIES, VALID_SOURCES

router = APIRouter(prefix="/api")


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


_TRACEBACK_PREFIX = re.compile(r"^Traceback\s*\(most recent call last\):\s*", re.IGNORECASE)
_PATH_RE = re.compile(r"(/[\w.\-]+)+")


def _safe_error(error: str | None) -> str | None:
    """
    A one-line, path-free summary. /api/stats is public; raw exception text
    leaks filesystem paths and internal structure, and helps nobody reading
    a status page.
    """
    if not error:
        return None
    first = error.strip().splitlines()[0]
    first = _TRACEBACK_PREFIX.sub("", first).strip()
    first = _PATH_RE.sub("<path>", first)
    return first[:120] or None


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
