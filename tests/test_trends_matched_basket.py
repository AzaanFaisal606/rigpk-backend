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
    """5 parts hold their price; 2 cheap arrivals show up only on day 2.

    Two arrivals (not one) matter: a composition-naive center (e.g. the old
    per-date trimmed-mean, k=ceil(n*0.10) trimmed off each end) survives a
    LONE outlier by trimming it away and landing back on the stable value by
    coincidence. With two cheap arrivals, trimming only removes one of them,
    so a composition-naive method is still pulled down -- while the matched
    basket (only the 5 parts present on both dates) stays exactly flat.
    """
    d1, d2 = "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z"
    db.upsert_products([_p(i, 200000, d1) for i in range(5)])
    db.upsert_products(
        [_p(i, 200000, d2) for i in range(5)]
        + [_p(90, 50000, d2), _p(91, 50000, d2)]
    )
    db.rebuild_price_trends()

    series = _series(db, "RTX 5070")
    assert len(series) == 2
    assert series[0][1] == 200000
    assert series[1][1] == 200000, (
        f"two cheap arrivals must not move the line: {series}"
    )


def test_a_real_price_drop_still_shows(db):
    """A genuine 10% drop across the matched basket, plus 4 expensive-only-on
    day-2 listings that would drag any composition-naive center (median or
    trimmed-mean over ALL that date's active listings) far above the true
    180000 -- while the matched basket (only the 3 parts present on both
    dates) reports the real drop exactly.
    """
    d1, d2 = "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z"
    db.upsert_products([_p(i, 200000, d1) for i in range(3)])
    db.upsert_products(
        [_p(i, 180000, d2) for i in range(3)]
        + [_p(90 + j, 900000, d2) for j in range(4)]
    )
    db.rebuild_price_trends()

    series = _series(db, "RTX 5070")
    assert series[0][1] == 200000
    assert series[1][1] == 180000
    assert series[1][1] < series[0][1]


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


def test_method_label_matches_the_arithmetic_that_produced_center_price(db):
    """Regression: method must describe the calculation that actually produced
    center_price, not just a listing-count threshold. Basket [10, 20, 30,
    1000, 1010] (scaled x1000 to clear the gpu price floor) has a median of
    30000 but a trimmed mean (ceil(5*0.10)=1 dropped each end) of 350000 --
    an 11x difference, so a mislabelled row is unmissable.
    """
    d1 = "2026-08-01T00:00:00Z"
    prices = [10000, 20000, 30000, 1000000, 1010000]
    db.upsert_products([_p(i, p, d1) for i, p in enumerate(prices)])
    db.rebuild_price_trends()

    row = db._conn.execute(
        "SELECT center_price, method FROM price_trends WHERE group_key = ?",
        ("RTX 5070",),
    ).fetchone()

    median = 30000
    trimmed_mean = round((20000 + 30000 + 1000000) / 3)
    assert median != trimmed_mean  # the basket must actually be asymmetric

    if row["method"] == "matched_basket_median":
        assert row["center_price"] == median
    elif "trimmed" in row["method"]:
        assert row["center_price"] == trimmed_mean
    else:
        pytest.fail(f"unexpected method label: {row['method']}")


def test_rebuild_is_idempotent(db):
    d1, d2 = "2026-08-01T00:00:00Z", "2026-08-08T00:00:00Z"
    db.upsert_products([_p(i, 200000, d1) for i in range(3)])
    db.upsert_products([_p(i, 190000, d2) for i in range(3)])
    first = db.rebuild_price_trends()
    before = _series(db, "RTX 5070")
    second = db.rebuild_price_trends()
    assert first == second
    assert _series(db, "RTX 5070") == before
