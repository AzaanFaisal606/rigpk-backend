from __future__ import annotations
import ipaddress
import json
import math
import os
import time
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, ConfigDict

from db.database import Database
from backend.deps import get_database

router = APIRouter(prefix="/api/builds")

# Per-client cap on share creation. One dyno, one process, so an in-memory
# bucket is sufficient — this stops casual abuse filling a metered database,
# not a determined distributed attacker. It is per-process state: it resets
# on every Render dyno restart/redeploy, so treat it as a speed bump, not a
# security control.
_RATE: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 20          # shares
_RATE_WINDOW = 300        # seconds
_RATE_PRUNE_INTERVAL = 3600  # seconds between full sweeps of stale keys
_last_prune = 0.0

# Render (the deploy target) proxies every request, and main.py wires no
# proxy-header middleware — so without this, `request.client.host` is always
# the proxy's address and every visitor shares one bucket. Default on because
# the deployed environment is always behind Render's proxy; set to a falsy
# value (0/false/no/off) for local/direct runs where nothing sits in front to
# set the header honestly.
#
# X-Forwarded-For is attacker-controlled the moment this app is reachable
# directly (bypassing the proxy) or the proxy doesn't overwrite an incoming
# copy of the header. Trusting it here does not authenticate the client — a
# spoofed value just buys the sender a fresh bucket, i.e. the worst case is
# evading the speed bump, not a privilege of any kind. That trade is fine
# behind Render (which does set the header honestly); it would not be fine
# if this app were ever exposed directly to the internet with the flag on.
_TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "true").strip().lower() not in (
    "0", "false", "no", "off", "",
)

# Longest possible IPv6 literal ("::ffff:255.255.255.255" territory included).
_MAX_FORWARDED_LEN = 45


def _client_key(request: Request) -> str:
    """Resolve the bucket key for `_check_rate`.

    Prefers the left-most `X-Forwarded-For` entry (the original client;
    Render appends its own hop as it forwards) when proxy headers are
    trusted, falling back to `request.client.host`. The header value is
    sanitised — length-capped and parsed as an IP — before it's ever used as
    a dict key, so a crafted header can't grow `_RATE` with unbounded junk
    keys between prune sweeps.
    """
    if _TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
            if candidate and len(candidate) <= _MAX_FORWARDED_LEN:
                try:
                    ipaddress.ip_address(candidate)
                    return candidate
                except ValueError:
                    pass
    return request.client.host if request.client else "unknown"


def _check_rate(request: Request) -> None:
    """Per-client cap on share creation, plus a periodic sweep of `_RATE` so a
    steady trickle of distinct clients (or a scraper cycling through spoofed
    ones) doesn't grow the dict forever between hits from the same client."""
    global _last_prune
    key = _client_key(request)
    now = time.monotonic()

    if now - _last_prune > _RATE_PRUNE_INTERVAL:
        for stale in [k for k, hits in _RATE.items() if not any(now - t < _RATE_WINDOW for t in hits)]:
            del _RATE[stale]
        _last_prune = now

    hits = [t for t in _RATE[key] if now - t < _RATE_WINDOW]
    if len(hits) >= _RATE_LIMIT:
        retry_after = max(1, math.ceil(hits[0] + _RATE_WINDOW - now))
        raise HTTPException(
            status_code=429,
            detail="Too many builds shared; try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)
    _RATE[key] = hits


class ShareBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu: Optional[int] = None
    gpu: Optional[int] = None
    ram: Optional[int] = None
    motherboard: Optional[int] = None
    psu: Optional[int] = None
    case: Optional[int] = None
    ssd: Optional[int] = None
    cooling: Optional[int] = None


class ShareBuildResponse(BaseModel):
    code: str


@router.post("/share", response_model=ShareBuildResponse)
def share_build(body: ShareBuildRequest, request: Request, db: Database = Depends(get_database)):
    _check_rate(request)
    build = {
        field: value
        for field, value in body.model_dump().items()
        if value is not None
    }
    if not build:
        raise HTTPException(status_code=400, detail="Build has no parts selected")

    statuses = db.resolve_part_status(list(build.values()))
    missing = [pid for pid in build.values() if pid not in statuses]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown part id(s): {missing}")

    code = db.create_shared_build(build)
    return {"code": code}


@router.get("/share/{code}")
def get_shared_build(code: str, db: Database = Depends(get_database)):
    if not code.isalnum() or len(code) != 6:
        raise HTTPException(status_code=400, detail="Invalid share code format")
    result = db.resolve_shared_build(code)
    if result is None:
        raise HTTPException(status_code=404, detail="Share code not found")
    # Parse specs JSON for each part (DB layer returns raw string)
    parsed = {}
    for slot, part in result.items():
        specs_raw = part.pop("specs", None)
        part["specs"] = json.loads(specs_raw) if specs_raw else None
        parsed[slot] = part
    return parsed
