from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from db.database import Database
from backend.deps import get_database
from backend.constants import VALID_CATEGORIES

router = APIRouter(prefix="/api/trends")

# Categories that have trend data (model axis for gpu/cpu, spec axis for ram).
_TREND_CATEGORIES = {"gpu", "cpu", "ram"}


class TrendPoint(BaseModel):
    scrape_date: str
    center_price: int
    min_price: int
    max_price: int
    sample_count: int


class TrendGroup(BaseModel):
    group_key: str
    latest_price: int
    min_price: int
    max_price: int
    sample_count: int
    thumbnail_url: Optional[str] = None
    series: list[TrendPoint]


class TrendGroupsResponse(BaseModel):
    category: str
    groups: list[TrendGroup]


def _validate(category: str) -> None:
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
    if category not in _TREND_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"No trends tracked for category: {category}",
        )


@router.get("/groups", response_model=TrendGroupsResponse)
def list_trend_groups(category: str = Query(...), db: Database = Depends(get_database)):
    """
    All trend groups for a category, each with its full price series, latest
    center/min/max and a representative thumbnail. One round-trip per category
    so the trends page can render a whole list (whitelist-filtered client-side).
    """
    _validate(category)
    groups = db.list_trend_groups(category)
    # full series for every group, bucketed by group_key
    series_rows = db.get_price_trends(category)
    by_group: dict[str, list[TrendPoint]] = {}
    for r in series_rows:
        by_group.setdefault(r["group_key"], []).append(
            TrendPoint(
                scrape_date=r["scrape_date"],
                center_price=r["center_price"],
                min_price=r["min_price"],
                max_price=r["max_price"],
                sample_count=r["sample_count"],
            )
        )
    # Series are capped to the most recent scrapes (db._TREND_MAX_DATES), so a
    # model that hasn't been listed since before that window has no points left.
    # Drop it rather than render a row with a price and an empty chart.
    out = [
        TrendGroup(
            group_key=g["group_key"],
            latest_price=g["latest_price"],
            min_price=g["min_price"],
            max_price=g["max_price"],
            sample_count=g["sample_count"],
            thumbnail_url=g.get("thumbnail_url"),
            series=by_group[g["group_key"]],
        )
        for g in groups
        if by_group.get(g["group_key"])
    ]
    return TrendGroupsResponse(category=category, groups=out)


@router.get("", response_model=list[TrendPoint])
def get_trend_series(
    category: str = Query(...),
    group_key: str = Query(...),
    db: Database = Depends(get_database),
):
    """Single group's price series (one model/spec over time)."""
    _validate(category)
    rows = db.get_price_trends(category, group_key=group_key)
    if not rows:
        raise HTTPException(status_code=404, detail="No trend data for group")
    return [
        TrendPoint(
            scrape_date=r["scrape_date"],
            center_price=r["center_price"],
            min_price=r["min_price"],
            max_price=r["max_price"],
            sample_count=r["sample_count"],
        )
        for r in rows
    ]
