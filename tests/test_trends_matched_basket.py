"""
Trends must report price movement, not catalogue churn.

Measured on live data, 83% of what the old method showed was products entering
and leaving a group. A cheap listing arriving read as a price drop; that is the
exact scenario pinned below.
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


def test_stable_prices_produce_a_flat_line_despite_a_cheap_arrival(db):
    """The headline case: 3 parts hold their price, a cheap 4th appears."""
    d1, d2 = "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z"
    db.upsert_products([_p(i, 200000, d1) for i in range(3)])
    db.upsert_products(
        [_p(i, 200000, d2) for i in range(3)] + [_p(99, 100000, d2)]
    )
    db.rebuild_price_trends()

    series = _series(db, "RTX 5070")
    assert len(series) == 2
    assert series[0][1] == series[1][1], (
        f"a cheap arrival must not move the line: {series}"
    )


def test_a_real_price_drop_still_shows(db):
    d1, d2 = "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z"
    db.upsert_products([_p(i, 200000, d1) for i in range(3)])
    db.upsert_products([_p(i, 180000, d2) for i in range(3)])
    db.rebuild_price_trends()

    series = _series(db, "RTX 5070")
    assert series[1][1] < series[0][1]
    assert series[1][1] == pytest.approx(180000, rel=0.02)


def test_basket_too_small_drops_the_point(db):
    """Fewer than 3 matched parts is not a measurement; do not publish it."""
    d1, d2 = "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z"
    db.upsert_products([_p(1, 200000, d1)])
    db.upsert_products([_p(1, 150000, d2), _p(2, 150000, d2)])
    db.rebuild_price_trends()
    assert len([s for s in _series(db, "RTX 5070") if s[0] == "2026-08-08"]) == 0


def test_first_date_anchors_at_the_real_price_level(db):
    d1 = "2026-08-01T00:00:00Z"
    db.upsert_products([_p(i, 200000, d1) for i in range(3)])
    db.upsert_products([_p(i, 200000, "2026-08-08T00:00:00Z") for i in range(3)])
    db.rebuild_price_trends()
    assert _series(db, "RTX 5070")[0][1] == pytest.approx(200000, rel=0.02)


def test_rebuild_is_idempotent(db):
    d1, d2 = "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z"
    db.upsert_products([_p(i, 200000, d1) for i in range(3)])
    db.upsert_products([_p(i, 190000, d2) for i in range(3)])
    first = db.rebuild_price_trends()
    before = _series(db, "RTX 5070")
    second = db.rebuild_price_trends()
    assert first == second
    assert _series(db, "RTX 5070") == before
