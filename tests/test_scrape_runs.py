"""
Tests for the scrape-run log that drives the landing page's STALE ribbon.

The rule under test: a retailer is stale when its *most recent* run failed, and
it clears itself the moment a run succeeds — no hand-maintained flag anywhere.
"""

from datetime import datetime, timedelta, timezone

import pytest

from db.database import Database


def ts(minutes_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


def test_no_runs_recorded_means_no_health_entry(db):
    assert db.source_health() == {}


def test_failed_run_marks_source_stale(db):
    db.record_scrape_run(
        "amdhouse.pk", started_at=ts(5), products=0, ok=False,
        error="returned 0 products",
    )
    health = db.source_health()["amdhouse.pk"]
    assert health["stale"] is True
    assert health["last_products"] == 0
    assert health["last_error"] == "returned 0 products"
    assert health["last_success_at"] is None


def test_successful_run_is_not_stale(db):
    db.record_scrape_run("czone.com.pk", started_at=ts(5), products=206, ok=True, swept=12)
    health = db.source_health()["czone.com.pk"]
    assert health["stale"] is False
    assert health["last_products"] == 206
    assert health["last_success_at"] == health["last_run_at"]


def test_later_success_clears_a_stale_flag(db):
    db.record_scrape_run("amdhouse.pk", started_at=ts(60), products=0, ok=False, error="429")
    db.record_scrape_run("amdhouse.pk", started_at=ts(5), products=527, ok=True, swept=3)
    assert db.source_health()["amdhouse.pk"]["stale"] is False


def test_later_failure_marks_stale_but_keeps_last_success(db):
    db.record_scrape_run("techmatched.pk", started_at=ts(120), products=653, ok=True)
    db.record_scrape_run("techmatched.pk", started_at=ts(5), products=0, ok=False, error="blocked")
    health = db.source_health()["techmatched.pk"]
    assert health["stale"] is True
    assert health["last_success_at"] is not None
    assert health["last_success_at"] < health["last_run_at"]


def test_parts_and_prebuilt_runs_are_tracked_separately(db):
    """redtech.pk sells both; a broken prebuilt scraper must not flag its parts."""
    db.record_scrape_run("redtech.pk", kind="parts", started_at=ts(5), products=107, ok=True)
    db.record_scrape_run("redtech.pk", kind="prebuilt", started_at=ts(5), products=0,
                         ok=False, error="404")

    assert db.source_health("parts")["redtech.pk"]["stale"] is False
    assert db.source_health("prebuilt")["redtech.pk"]["stale"] is True


def test_stats_exposes_source_health(db):
    db.record_scrape_run("amdhouse.pk", started_at=ts(5), products=0, ok=False, error="429")
    assert db.stats()["sources"]["amdhouse.pk"]["stale"] is True
