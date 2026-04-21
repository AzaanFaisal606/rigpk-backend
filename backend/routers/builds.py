from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from db.database import get_db

router = APIRouter(prefix="/api/builds")

DB_PATH = Path(__file__).parent.parent.parent / "data" / "ppc.db"

VALID_SLOTS = {"cpu", "gpu", "ram", "motherboard", "psu", "case", "ssd", "cooling"}


class ShareBuildRequest(BaseModel):
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
def share_build(body: ShareBuildRequest):
    build = {
        slot: getattr(body, slot)
        for slot in VALID_SLOTS
        if getattr(body, slot) is not None
    }
    if not build:
        raise HTTPException(status_code=400, detail="Build has no parts selected")
    with get_db(DB_PATH) as db:
        code = db.create_shared_build(build)
    return {"code": code}


@router.get("/share/{code}")
def get_shared_build(code: str):
    if not code.isalnum() or len(code) != 6:
        raise HTTPException(status_code=400, detail="Invalid share code format")
    with get_db(DB_PATH) as db:
        result = db.resolve_shared_build(code)
    if result is None:
        raise HTTPException(status_code=404, detail="Share code not found")
    # Parse specs JSON for each part (DB layer returns raw string)
    for slot, part in result.items():
        specs_raw = part.get("specs")
        part["specs"] = json.loads(specs_raw) if isinstance(specs_raw, str) else specs_raw
    return result
