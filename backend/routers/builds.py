from __future__ import annotations
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db.database import get_db
from backend.config import DB_PATH

router = APIRouter(prefix="/api/builds")

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
        field: value
        for field, value in body.model_dump().items()
        if value is not None
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
    parsed = {}
    for slot, part in result.items():
        specs_raw = part.pop("specs", None)
        part["specs"] = json.loads(specs_raw) if specs_raw else None
        parsed[slot] = part
    return parsed
