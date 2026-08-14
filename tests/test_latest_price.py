"""
parts.latest_price caches the newest price_log price for each part.

price_log remains the source of truth — this column exists only to remove a
correlated subquery from every list query. Any divergence is a bug, so the
integrity check asserts they agree.
"""
import pytest

from db.database import Database


def _product(price, at, url="https://example.com/p/rtx-5090"):
    return {
        "name": "RTX 5090 Test Card", "price_pkr": price, "url": url,
        "category": "gpu", "source": "czone", "scraped_at": at,
    }


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_insert_sets_latest_price(db):
    db.upsert_products([_product(500000, "2026-08-01T00:00:00Z")])
    row = db._conn.execute("SELECT latest_price FROM parts").fetchone()
    assert row["latest_price"] == 500000


def test_update_moves_latest_price_forward(db):
    db.upsert_products([_product(500000, "2026-08-01T00:00:00Z")])
    db.upsert_products([_product(465000, "2026-08-08T00:00:00Z")])
    row = db._conn.execute("SELECT latest_price FROM parts").fetchone()
    assert row["latest_price"] == 465000


def test_latest_price_matches_newest_price_log_row(db):
    db.upsert_products([_product(500000, "2026-08-01T00:00:00Z")])
    db.upsert_products([_product(465000, "2026-08-08T00:00:00Z")])
    db.upsert_products([_product(470000, "2026-08-15T00:00:00Z")])
    drift = db._conn.execute(
        """
        SELECT COUNT(*) AS n FROM parts p
        WHERE p.latest_price IS NULL
           OR p.latest_price <> (
               SELECT price_pkr FROM price_log
               WHERE part_id = p.id AND price_pkr IS NOT NULL
               ORDER BY scraped_at DESC, id DESC LIMIT 1
           )
        """
    ).fetchone()["n"]
    assert drift == 0


def test_migrate_adds_column_to_existing_db(tmp_path):
    """A DB created before the column existed must gain it on next open.

    The legacy table below includes is_active/last_seen_at/name_norm — the
    columns that predate latest_price in real DB history — because
    schema.sql's CREATE INDEX idx_parts_category_active references
    is_active and runs inside the same executescript() as CREATE TABLE.
    A table missing is_active entirely fails there before _migrate() ever
    runs; that's a separate, pre-existing gap unrelated to latest_price.
    """
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE parts (id INTEGER PRIMARY KEY, source TEXT, source_id TEXT, "
        "name TEXT, category TEXT, url TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
        "last_seen_at TEXT, name_norm TEXT)"
    )
    con.commit()
    con.close()

    d = Database(path)
    cols = {r["name"] for r in d._conn.execute("PRAGMA table_info(parts)").fetchall()}
    d.close()
    assert "latest_price" in cols
