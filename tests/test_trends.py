"""Unit tests for price-trend aggregation helpers and rebuild_price_trends."""
import tempfile
from pathlib import Path

import pytest

from db.database import _median, _trimmed_mean, get_db


# ── helpers ──────────────────────────────────────────────────────────────────

def test_median_odd():
    assert _median([3, 1, 2]) == 2

def test_median_even():
    assert _median([1, 2, 3, 4]) == 2.5

def test_median_single():
    assert _median([42]) == 42

def test_trimmed_mean_drops_outliers():
    # 10 values 100..1000; ceil(10*0.1)=1 dropped each end -> mean of 200..900
    vals = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    mean, kept = _trimmed_mean(vals, 0.10)
    assert kept == 8
    assert mean == sum([200, 300, 400, 500, 600, 700, 800, 900]) / 8

def test_trimmed_mean_extreme_outlier_pulled():
    # one absurd value gets trimmed away
    vals = [100, 100, 100, 100, 100, 999999]
    mean, kept = _trimmed_mean(vals, 0.10)  # ceil(6*0.1)=1 each end
    assert 999999 not in range(int(mean), int(mean) + 1)
    assert mean == 100  # outlier + one low both trimmed, rest all 100

def test_trimmed_mean_never_empty():
    mean, kept = _trimmed_mean([5, 5], 0.10)
    assert kept >= 1


# ── rebuild_price_trends (end-to-end on a temp DB) ───────────────────────────

@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = get_db(Path(d) / "t.db")
        yield database
        database.close()


def _seed(db, name, category, prices_by_date):
    """Insert one part + price_log rows. prices_by_date: {date: price}."""
    cur = db._conn.cursor()
    from scrapers.spec_extractor import extract_specs
    import json
    specs = extract_specs(name, category)
    specs_json = json.dumps(specs) if specs else None
    pid = cur.execute(
        "INSERT INTO parts (source, source_id, name, category, url, specs) "
        "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        ("test.pk", name, name, category, f"http://x/{name}", specs_json),
    ).fetchone()["id"]
    for date, price in prices_by_date.items():
        cur.execute(
            "INSERT INTO price_log (part_id, price_pkr, scraped_at) VALUES (?, ?, ?)",
            (pid, price, f"{date}T00:00:00Z"),
        )
    db._conn.commit()


def test_rebuild_groups_gpu_models(db):
    # 6 RTX 4070 listings on one date -> trimmed_mean bucket
    for i in range(6):
        _seed(db, f"MSI RTX 4070 variant {i}", "gpu", {"2026-01-01": 200000 + i * 1000})
    n = db.rebuild_price_trends()
    assert n == 1
    series = db.get_price_trends("gpu", "RTX 4070")
    assert len(series) == 1
    row = series[0]
    assert row["method"] == "trimmed_mean"
    assert row["sample_count"] == 6
    # band is 5%-trimmed (ceil(6*.05)=1 dropped each end): 200000 and 205000 shed
    assert row["min_price"] == 201000
    assert row["max_price"] == 204000

def test_band_trims_lone_outlier(db):
    # 8 sane RTX 5080 listings + 1 absurd 9,999,999 typo -> band must not blow out
    for i in range(8):
        _seed(db, f"Gigabyte RTX 5080 v{i}", "gpu", {"2026-01-01": 500000 + i * 1000})
    _seed(db, "Gigabyte RTX 5080 typo", "gpu", {"2026-01-01": 9999999})
    db.rebuild_price_trends()
    row = db.get_price_trends("gpu", "RTX 5080")[0]
    assert row["sample_count"] == 9
    assert row["max_price"] < 1000000  # 9,999,999 outlier trimmed off the band

def test_small_bucket_band_untrimmed(db):
    # n<5 (median bucket) keeps its full range — too few points to trim a band
    for i, p in enumerate([100000, 110000, 130000]):
        _seed(db, f"MSI RTX 5070 v{i}", "gpu", {"2026-01-01": p})
    db.rebuild_price_trends()
    row = db.get_price_trends("gpu", "RTX 5070")[0]
    assert row["min_price"] == 100000 and row["max_price"] == 130000

def test_rebuild_small_bucket_uses_median(db):
    # 3 listings -> below trim threshold -> median fallback
    for i, p in enumerate([100000, 110000, 300000]):
        _seed(db, f"MSI RTX 5070 v{i}", "gpu", {"2026-01-01": p})
    db.rebuild_price_trends()
    row = db.get_price_trends("gpu", "RTX 5070")[0]
    assert row["method"] == "median"
    assert row["center_price"] == 110000  # median of 3, robust to the 300k outlier
    assert row["min_price"] == 100000 and row["max_price"] == 300000

def test_rebuild_ram_spec_axis(db):
    for i in range(5):
        _seed(db, f"Corsair 16GB DDR4 3200MHz kit {i}", "ram", {"2026-01-01": 8000 + i * 100})
    db.rebuild_price_trends()
    series = db.get_price_trends("ram", "DDR4-3200-16GB", group_type="spec")
    assert len(series) == 1
    assert series[0]["sample_count"] == 5


def test_ram_capacity_separate_buckets(db):
    # 16GB and 32GB of the same DDR/speed must NOT share a bucket
    for i in range(5):
        _seed(db, f"Corsair 16GB DDR4 3200MHz v{i}", "ram", {"2026-01-01": 8000})
    for i in range(5):
        _seed(db, f"Corsair 32GB 2x16GB DDR4 3200MHz v{i}", "ram", {"2026-01-01": 16000})
    db.rebuild_price_trends()
    groups = {g["group_key"] for g in db.list_trend_groups("ram")}
    assert "DDR4-3200-16GB" in groups
    assert "DDR4-3200-32GB" in groups

def test_ram_excludes_8gb_and_oddball_speed(db):
    # 8GB capacity -> not tracked
    for i in range(5):
        _seed(db, f"Corsair 8GB DDR4 3200MHz v{i}", "ram", {"2026-01-01": 4000})
    # 16GB but a one-off/overclock speed not on the standard list -> not tracked
    for i in range(5):
        _seed(db, f"Corsair 16GB DDR5 8000MHz v{i}", "ram", {"2026-01-01": 90000})
    n = db.rebuild_price_trends()
    assert n == 0

def test_rebuild_idempotent(db):
    for i in range(6):
        _seed(db, f"MSI RTX 4070 v{i}", "gpu", {"2026-01-01": 200000})
    first = db.rebuild_price_trends()
    second = db.rebuild_price_trends()
    assert first == second
    # no duplicate rows
    cnt = db._conn.execute("SELECT COUNT(*) FROM price_trends").fetchone()[0]
    assert cnt == first

def test_rebuild_multiple_dates_make_series(db):
    _seed(db, "MSI RTX 4070 a", "gpu", {"2026-01-01": 210000, "2026-02-01": 205000, "2026-03-01": 200000})
    for i in range(5):
        _seed(db, f"MSI RTX 4070 b{i}", "gpu", {"2026-01-01": 210000, "2026-02-01": 205000})
    db.rebuild_price_trends()
    series = db.get_price_trends("gpu", "RTX 4070")
    dates = [r["scrape_date"] for r in series]
    assert dates == ["2026-01-01", "2026-02-01", "2026-03-01"]

def test_list_trend_groups_latest(db):
    _seed(db, "MSI RTX 4070 a", "gpu", {"2026-01-01": 220000, "2026-02-01": 200000})
    for i in range(5):
        _seed(db, f"MSI RTX 4070 b{i}", "gpu", {"2026-01-01": 220000, "2026-02-01": 200000})
    db.rebuild_price_trends()
    groups = db.list_trend_groups("gpu")
    assert len(groups) == 1
    assert groups[0]["group_key"] == "RTX 4070"
    # latest_price comes from the most recent date (2026-02-01), ~200000
    assert groups[0]["latest_price"] == 200000

def test_untrendable_parts_skipped(db):
    # GT 730 not on allowlist -> no model -> no trend row
    _seed(db, "MSI GeForce GT 730 4GB", "gpu", {"2026-01-01": 15000})
    n = db.rebuild_price_trends()
    assert n == 0


def test_same_day_rescrape_not_double_counted(db):
    """A part scraped twice on the same calendar date counts once (latest)."""
    import json as _json
    from scrapers.spec_extractor import extract_specs
    cur = db._conn.cursor()
    # 5 distinct RTX 5070 listings, each scraped once
    for i in range(5):
        name = f"MSI RTX 5070 v{i}"
        specs = _json.dumps(extract_specs(name, "gpu"))
        pid = cur.execute(
            "INSERT INTO parts (source, source_id, name, category, url, specs) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            ("test.pk", name, name, "gpu", f"http://x/{name}", specs),
        ).fetchone()["id"]
        cur.execute(
            "INSERT INTO price_log (part_id, price_pkr, scraped_at) VALUES (?, ?, ?)",
            (pid, 100000, "2026-01-01T08:00:00Z"),
        )
        # one of them re-scraped later the SAME day at a different price
        if i == 0:
            cur.execute(
                "INSERT INTO price_log (part_id, price_pkr, scraped_at) VALUES (?, ?, ?)",
                (pid, 999999, "2026-01-01T20:00:00Z"),
            )
    db._conn.commit()
    db.rebuild_price_trends()
    row = db.get_price_trends("gpu", "RTX 5070")[0]
    # 5 listings, not 6 — the same-day re-scrape collapses to its latest row.
    # (If it had double-counted, sample_count would be 6.)
    assert row["sample_count"] == 5
    # The re-scrape kept the latest price (999999, not 100000). With dedup it's
    # one listing of 999999 among four 100000s; the 5% band trim sheds that lone
    # high outlier, so the band stays at 100000 — proving the value isn't doubled
    # AND that the band is outlier-robust.
    assert row["max_price"] == 100000


def test_get_price_trends_ram_default_group_type(db):
    """RAM trends are reachable without passing group_type explicitly."""
    for i in range(5):
        _seed(db, f"Corsair 16GB DDR4 3200MHz kit {i}", "ram", {"2026-01-01": 8000 + i * 100})
    db.rebuild_price_trends()
    # no group_type arg — must still find the 'spec'-axis RAM bucket
    series = db.get_price_trends("ram", "DDR4-3200-16GB")
    assert len(series) == 1
    groups = db.list_trend_groups("ram")
    assert groups and groups[0]["group_key"] == "DDR4-3200-16GB"


def test_median_even_used_count(db):
    """Even-length median bucket reports used_count=2, not the full n."""
    for i, p in enumerate([100000, 110000, 120000, 130000]):  # n=4 (even, <5 -> median)
        _seed(db, f"MSI RTX 5080 v{i}", "gpu", {"2026-01-01": p})
    db.rebuild_price_trends()
    row = db.get_price_trends("gpu", "RTX 5080")[0]
    assert row["method"] == "median"
    assert row["sample_count"] == 4
    assert row["used_count"] == 2
