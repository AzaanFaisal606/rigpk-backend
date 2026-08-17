"""
Schema and write-path invariants, against an isolated tmp DB.

This file used to be a __main__ script with a hardcoded local path: its checks
never ran under pytest and never touched the live DB, so it verified nothing in
either direction. The operational checks (recent-scrape freshness, per-source
active counts) moved to scripts/check_db_integrity.py, which is meaningful only
against a live target.
"""
import pytest

from db.database import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.upsert_products([
        {"name": f"Asus GeForce RTX 4060 {i}", "price_pkr": 80000 + i,
         "url": f"https://czone.com.pk/product/rtx-4060-{i}", "category": "gpu",
         "source": "czone", "scraped_at": "2026-08-14T00:00:00Z"}
        for i in range(20)
    ])
    yield d
    d.close()


def test_no_orphaned_price_log_rows(db):
    n = db._conn.execute(
        "SELECT COUNT(*) FROM price_log pl "
        "LEFT JOIN parts p ON p.id = pl.part_id WHERE p.id IS NULL"
    ).fetchone()[0]
    assert n == 0


def test_latest_price_matches_price_log(db):
    n = db._conn.execute(
        "SELECT COUNT(*) FROM parts p WHERE p.latest_price IS NOT ("
        "  SELECT price_pkr FROM price_log WHERE part_id = p.id "
        "  AND price_pkr IS NOT NULL ORDER BY scraped_at DESC, id DESC LIMIT 1)"
    ).fetchone()[0]
    assert n == 0


def test_every_active_part_is_searchable(db):
    """A NULL name_norm makes a row invisible to search — silently."""
    n = db._conn.execute(
        "SELECT COUNT(*) FROM parts WHERE is_active = 1 AND name_norm IS NULL"
    ).fetchone()[0]
    assert n == 0


def test_no_duplicate_source_ids(db):
    n = db._conn.execute(
        "SELECT COUNT(*) FROM (SELECT source, source_id FROM parts "
        "GROUP BY source, source_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert n == 0


def test_inactive_parts_keep_their_history(db):
    db.upsert_products([{
        "name": "Asus GeForce RTX 4060 0", "price_pkr": 79000,
        "url": "https://czone.com.pk/product/rtx-4060-0", "category": "gpu",
        "source": "czone", "scraped_at": "2026-08-21T00:00:00Z"}])
    db.deactivate_unseen_parts("czone")
    swept = db._conn.execute(
        "SELECT id FROM parts WHERE is_active = 0 LIMIT 1"
    ).fetchone()
    assert swept is not None
    history = db._conn.execute(
        "SELECT COUNT(*) FROM price_log WHERE part_id = ?", (swept["id"],)
    ).fetchone()[0]
    assert history > 0, "a swept part must keep its price history"
