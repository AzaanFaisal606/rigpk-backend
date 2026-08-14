from __future__ import annotations
import json
import time
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, ConfigDict

from db.database import Database
from backend.deps import get_database

router = APIRouter(prefix="/api/builds")

# Per-IP cap on share creation. One dyno, one process, so an in-memory bucket
# is sufficient — this stops casual abuse filling a metered database, not a
# determined distributed attacker. It is per-process state: it resets on
# every Render dyno restart/redeploy, so treat it as a speed bump, not a
# security control.
_RATE: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 20          # shares
_RATE_WINDOW = 300        # seconds
_RATE_PRUNE_INTERVAL = 3600  # seconds between full sweeps of stale IPs
_last_prune = 0.0


def _check_rate(request: Request) -> None:
    """Per-IP cap on share creation, plus a periodic sweep of `_RATE` so a
    steady trickle of distinct IPs (or a scraper cycling through spoofed
    ones) doesn't grow the dict forever between hits from the same IP."""
    global _last_prune
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()

    if now - _last_prune > _RATE_PRUNE_INTERVAL:
        for key in [k for k, hits in _RATE.items() if not any(now - t < _RATE_WINDOW for t in hits)]:
            del _RATE[key]
        _last_prune = now

    hits = [t for t in _RATE[ip] if now - t < _RATE_WINDOW]
    if len(hits) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many builds shared; try again later.")
    hits.append(now)
    _RATE[ip] = hits


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
