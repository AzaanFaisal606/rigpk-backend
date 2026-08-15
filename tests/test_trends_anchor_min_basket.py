"""
G7: the i == 0 anchor row of every trend series bypassed _TREND_MIN_BASKET,
so a group with a single listing on its first date emitted a series point
with basket_size == 1. The anchor sets the level for the whole chained
series, so a one-product anchor undercuts the churn-neutrality the
matched-basket rewrite was built for just as much as an unguarded mid-series
point would.

Decision: a too-small first date is skipped, not the whole series dropped —
the series starts at the first date whose own basket meets
_TREND_MIN_BASKET (3). A group that never reaches that basket size on any
date produces no series at all, which falls out of the same rule rather than
needing separate handling.
"""
import pytest

from db.database import Database


def _p(i, price, at, category="gpu", name=None):
    return {
        "name": name or f"RTX 5070 Card {i}",
        "price_pkr": price,
        "url": f"https://example.com/p/gpu-{i}",
        "category": category,
        "source": "czone",
        "scraped_at": at,
    }


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _series(db, group_key):
    rows = db._conn.execute(
        "SELECT scrape_date, center_price, basket_size FROM price_trends "
        "WHERE group_key = ? ORDER BY scrape_date",
        (group_key,),
    ).fetchall()
    return [(r["scrape_date"], r["center_price"], r["basket_size"]) for r in rows]


def test_single_listing_first_date_produces_no_series_at_all(db):
    """The group never reaches the minimum basket on any date."""
    d1 = "2026-08-01T00:00:00Z"
    db.upsert_products([_p(0, 200000, d1)])
    db.rebuild_price_trends()

    assert _series(db, "RTX 5070") == []


def test_series_starts_at_first_qualifying_date_not_the_anchor_date(db):
    """
    Day 1 has one listing (below the minimum) — must not appear. Day 2 grows
    to 3 listings and becomes the real anchor.
    """
    d1, d2 = "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z"
    db.upsert_products([_p(0, 999999, d1)])  # would-be anchor if unguarded
    db.upsert_products([_p(i, 200000, d2) for i in range(3)])
    db.rebuild_price_trends()

    series = _series(db, "RTX 5070")
    assert len(series) == 1
    date, price, basket_size = series[0]
    assert date == "2026-08-08"
    assert basket_size == 3
    assert price == 200000


def test_basket_growing_past_the_minimum_chains_from_the_real_anchor(db):
    """Day 1 under the minimum (skipped), day 2 anchors at 3, day 3 chains."""
    d1, d2, d3 = "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z", "2026-08-15T00:00:00Z"
    db.upsert_products([_p(0, 999999, d1)])
    db.upsert_products([_p(i, 200000, d2) for i in range(3)])
    db.upsert_products([_p(i, 220000, d3) for i in range(3)])
    db.rebuild_price_trends()

    series = _series(db, "RTX 5070")
    assert [s[0] for s in series] == ["2026-08-08", "2026-08-15"]
    assert series[0][1] == 200000
    assert series[1][1] == 220000
    assert series[0][2] == 3
    assert series[1][2] == 3
