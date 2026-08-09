from __future__ import annotations
import json
from typing import Any, Optional
from fastapi import APIRouter, Query, HTTPException, Response
from pydantic import BaseModel

from db.database import get_db
from backend.config import DB_PATH
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


@router.get("/stats")
def get_stats():
    with get_db(DB_PATH) as db:
        return db.stats()


@router.get("/parts/filters")
def get_filter_options(category: str = Query(...)):
    if category not in VALID_CATEGORIES:
        return {}
    with get_db(DB_PATH) as db:
        return db.get_filter_options(category)


@router.get("/search-index")
def get_search_index(response: Response, category: str = Query(...)):
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category '{category}'")
    with get_db(DB_PATH) as db:
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
):
    if category and category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category '{category}'. Valid: {sorted(VALID_CATEGORIES)}")
    if source and source not in VALID_SOURCES:
        source = None

    id_list: Optional[list[int]] = None
    if ids is not None:
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="ids must be comma-separated integers")
        if len(id_list) > 50:
            raise HTTPException(status_code=400, detail="ids accepts at most 50 values")

    raw_spec_filters = {
        "brand": brand, "socket": socket, "model": model, "vram": vram,
        "ddr_type": ddr_type, "speed": speed, "chipset": chipset,
        "wattage": wattage, "rating": rating, "form_factor": form_factor,
        "type": cooling_type, "aio_size": aio_size, "fan_size": fan_size,
        "interface": interface, "capacity": capacity,
    }
    specs_filter = {k: v for k, v in raw_spec_filters.items() if v is not None} or None

    with get_db(DB_PATH) as db:
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
