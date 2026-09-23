"""
Trends move once a week.

Only `run_all.py --weekly` (the Friday cron) stamps rows with the real date and
rebuilds price_trends. Any other run is pinned to the latest trend date, so a
midweek fix can write to prod without adding a stray point to every chart.
"""
import os
import sys

import pytest

import run_all
from db.database import get_db


def _row(i: int, price: int, scraped_at: str) -> dict:
    return {
        "name": f"NVIDIA GeForce RTX 4070 12GB #{i}", "price_pkr": price,
        "url": f"https://czone.com.pk/product/rtx-4070-{i}", "category": "gpu",
        "source": "czone.com.pk", "scraped_at": scraped_at, "thumbnail_url": None,
    }


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """A DB whose latest trend date is 2026-09-18, and a fake czone scraper."""
    db_path = tmp_path / "ppc.db"
    with get_db(str(db_path)) as db:
        db.upsert_products([_row(i, 100_000, "2026-09-18T00:10:00+00:00") for i in range(4)])
        db.rebuild_price_trends()

    def fake_run_czone():
        # Stamps rows the way every real scraper does, via scraped_at_now().
        from scrapers.base_scraper import scraped_at_now
        return [_row(i, 90_000, scraped_at_now()) for i in range(4)]

    monkeypatch.setattr(run_all, "DB_PATH", str(db_path))
    monkeypatch.setattr(run_all, "SCRAPERS", {"czone": ("czone.com.pk", fake_run_czone)})
    monkeypatch.setattr(run_all, "backup_db", lambda *_a, **_k: None)
    return db_path


def _trend_dates(db_path) -> list[str]:
    with get_db(str(db_path)) as db:
        return [r[0] for r in db._conn.execute(
            "SELECT DISTINCT scrape_date FROM price_trends ORDER BY scrape_date").fetchall()]


def _log_dates(db_path) -> list[str]:
    with get_db(str(db_path)) as db:
        return [r[0] for r in db._conn.execute(
            "SELECT DISTINCT substr(scraped_at, 1, 10) FROM price_log ORDER BY 1").fetchall()]


def test_non_weekly_run_pins_to_latest_date_and_skips_rebuild(seeded, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_all.py", "czone"])
    run_all.main()

    assert _log_dates(seeded) == ["2026-09-18"], "a midweek run must not open a new date"
    assert _trend_dates(seeded) == ["2026-09-18"]
    with get_db(str(seeded)) as db:
        center = db._conn.execute("SELECT center_price FROM price_trends").fetchone()[0]
        live = {r[0] for r in db._conn.execute("SELECT latest_price FROM parts").fetchall()}
    assert live == {90_000}, "prices still go live"
    assert center == 100_000, "the trend point waits for the weekly rebuild"


def test_weekly_run_opens_a_new_date_and_rebuilds(seeded, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_all.py", "czone", "--weekly"])
    run_all.main()

    dates = _trend_dates(seeded)
    assert len(dates) == 2 and dates[0] == "2026-09-18"
    assert os.getenv("SCRAPE_AS_OF_DATE") is None


def test_pin_respects_explicit_as_of_date(seeded, monkeypatch):
    monkeypatch.setenv("SCRAPE_AS_OF_DATE", "2026-09-11")
    with get_db(str(seeded)) as db:
        assert run_all.pin_trend_bucket(db, weekly=False) == "2026-09-11"


def test_pin_on_empty_db_is_a_no_op(tmp_path):
    with get_db(str(tmp_path / "empty.db")) as db:
        assert run_all.pin_trend_bucket(db, weekly=False) is None
    assert os.getenv("SCRAPE_AS_OF_DATE") is None
