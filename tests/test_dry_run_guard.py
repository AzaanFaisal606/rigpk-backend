"""
SCRAPE_NO_DB_WRITE=1 must make data commits a no-op on BOTH backends.

It previously worked only on Turso: sqlite3.Connection.commit is read-only, so
assigning over it raised AttributeError and the guard crashed the process it
was meant to protect.
"""
import pytest

from db.database import Database


def test_dry_run_does_not_persist_writes(tmp_path, monkeypatch):
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("SCRAPE_NO_DB_WRITE", "1")

    db = Database(db_file)          # must not raise
    db.upsert_products([{
        "name": "RTX 5090 Test Card", "price_pkr": 500000,
        "url": "https://example.com/p/rtx-5090", "category": "gpu",
        "source": "czone", "scraped_at": "2026-08-14T00:00:00Z",
    }])
    db.close()

    monkeypatch.delenv("SCRAPE_NO_DB_WRITE")
    fresh = Database(db_file)
    count = fresh.stats()["total_parts"]
    fresh.close()
    assert count == 0, "dry run must not persist rows"


def test_schema_still_applied_in_dry_run(tmp_path, monkeypatch):
    """Schema/migrations commit BEFORE the guard installs, so the tables exist."""
    monkeypatch.setenv("SCRAPE_NO_DB_WRITE", "1")
    db = Database(tmp_path / "t.db")
    tables = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    db.close()
    assert {"parts", "price_log", "price_trends", "scrape_runs"} <= tables


def test_normal_mode_still_persists(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRAPE_NO_DB_WRITE", raising=False)
    db_file = tmp_path / "t.db"
    db = Database(db_file)
    db.upsert_products([{
        "name": "RTX 5090 Test Card", "price_pkr": 500000,
        "url": "https://example.com/p/rtx-5090", "category": "gpu",
        "source": "czone", "scraped_at": "2026-08-14T00:00:00Z",
    }])
    db.close()
    fresh = Database(db_file)
    assert fresh.stats()["total_parts"] == 1
    fresh.close()
