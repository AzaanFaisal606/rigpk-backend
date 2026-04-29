from __future__ import annotations
import json
from typing import Optional, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from db.database import get_db
from backend.config import DB_PATH

router = APIRouter(prefix="/api")


class PrebuiltItem(BaseModel):
    id: int
    name: str
    source: str
    url: str
    thumbnail_url: Optional[str]
    price_pkr: Optional[int]
    components: Optional[dict[str, Any]]
    scraped_at: str


class PrebuiltsResponse(BaseModel):
    items: list[PrebuiltItem]
    total: int


def _parse_components(raw: Any) -> Optional[dict]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return None


def _row_to_item(row: dict) -> PrebuiltItem:
    return PrebuiltItem(
        id=row["id"],
        name=row["name"],
        source=row["source"],
        url=row["url"],
        thumbnail_url=row.get("thumbnail_url"),
        price_pkr=row.get("price_pkr"),
        components=_parse_components(row.get("components")),
        scraped_at=row.get("scraped_at", ""),
    )


@router.get("/prebuilts", response_model=PrebuiltsResponse)
def list_prebuilts(
    source:     Optional[str] = Query(None),
    min_price:  Optional[int] = Query(None, ge=0),
    max_price:  Optional[int] = Query(None, ge=0),
    q:          Optional[str] = Query(None),
    cpu_brand:  Optional[str] = Query(None),   # "amd" | "intel"
    gpu_brand:  Optional[str] = Query(None),   # "amd" | "nvidia" | "intel"
    sort:       Optional[str] = Query(None, pattern="^price_(asc|desc)$"),   # "price_asc" | "price_desc"
    limit:      int           = Query(50, ge=1, le=200),
    offset:     int           = Query(0, ge=0),
):
    with get_db(DB_PATH) as db:
        items, total = db.list_prebuilts(
            source=source,
            min_price=min_price,
            max_price=max_price,
            q=q,
            cpu_brand=cpu_brand,
            gpu_brand=gpu_brand,
            sort=sort or "price_asc",
            limit=limit,
            offset=offset,
        )
    return PrebuiltsResponse(
        items=[_row_to_item(r) for r in items],
        total=total,
    )


@router.get("/prebuilts/{item_id}", response_model=PrebuiltItem)
def get_prebuilt(item_id: int):
    with get_db(DB_PATH) as db:
        row = db.get_prebuilt(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Prebuilt not found")
    return _row_to_item(row)
