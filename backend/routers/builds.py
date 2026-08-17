from __future__ import annotations
import ipaddress
import json
import math
import os
import time
from collections import defaultdict
from typing import Any, Optional, Union

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


# Per-slot value accepted from the client: either a bare part id (legacy
# shorthand, treated as qty=1) or an object with id/qty. `qty` is capped at
# 4 server-side below -- an unbounded qty would be a trivially abusable
# multiplier on this public endpoint. Any `price_at_share` the client sends
# is ignored; it's always taken from the server's own latest_price.
SlotValue = Union[int, dict]

_MIN_QTY = 1
_MAX_QTY = 4


class ShareBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu: Optional[SlotValue] = None
    gpu: Optional[SlotValue] = None
    ram: Optional[SlotValue] = None
    motherboard: Optional[SlotValue] = None
    psu: Optional[SlotValue] = None
    case: Optional[SlotValue] = None
    ssd: Optional[SlotValue] = None
    cooling: Optional[SlotValue] = None


class ShareBuildResponse(BaseModel):
    code: str


def _normalize_slot(slot: str, value: SlotValue) -> dict:
    """
    Normalize one slot's request value into {"id": int, "qty": int}.

    Accepts a bare int (legacy shorthand -> qty=1) or an object with
    id/qty, and is liberal about extra keys an object might carry (e.g. a
    client echoing back a price_at_share it received earlier) -- those are
    simply ignored, never trusted. Raises HTTPException(400) for anything
    that doesn't resolve to a real int id or a qty in 1..4.
    """
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"Invalid part id for slot '{slot}'")
    if isinstance(value, int):
        return {"id": value, "qty": 1}
    if isinstance(value, dict):
        part_id = value.get("id")
        qty = value.get("qty", 1)
        if isinstance(part_id, bool) or not isinstance(part_id, int):
            raise HTTPException(status_code=400, detail=f"Invalid part id for slot '{slot}'")
        if isinstance(qty, bool) or not isinstance(qty, int) or not (_MIN_QTY <= qty <= _MAX_QTY):
            raise HTTPException(
                status_code=400,
                detail=f"qty for slot '{slot}' must be an integer between {_MIN_QTY} and {_MAX_QTY}",
            )
        return {"id": part_id, "qty": qty}
    raise HTTPException(status_code=400, detail=f"Invalid value for slot '{slot}'")


@router.post("/share", response_model=ShareBuildResponse)
def share_build(body: ShareBuildRequest, request: Request, db: Database = Depends(get_database)):
    _check_rate(request)
    raw = {
        field: value
        for field, value in body.model_dump().items()
        if value is not None
    }
    if not raw:
        raise HTTPException(status_code=400, detail="Build has no parts selected")

    build = {slot: _normalize_slot(slot, value) for slot, value in raw.items()}

    statuses = db.resolve_part_status([slot["id"] for slot in build.values()])
    missing = [slot["id"] for slot in build.values() if slot["id"] not in statuses]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown part id(s): {missing}")

    # price_at_share always comes from the server's current latest_price --
    # never from the client, which could send any number it likes.
    for slot in build.values():
        slot["price_at_share"] = statuses[slot["id"]]["latest_price"]

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
