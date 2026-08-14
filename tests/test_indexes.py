"""
The market page's hot query must resolve through an index, not a full scan.

Also guards the libSQL divergence recorded in AUDIT.md: CREATE INDEX ... DESC
is silently skipped inside executescript(), so any DESC index must be issued
standalone and verified by read-back.
"""
import pytest

from db.database import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_expected_indexes_exist(db):
    names = {
        r["name"] for r in db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_parts_cat_active_price" in names


def test_market_query_uses_an_index(db):
    plan = db._conn.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT id FROM parts WHERE category='gpu' AND is_active=1 "
        "AND latest_price IS NOT NULL ORDER BY latest_price DESC, id ASC"
    ).fetchall()
    text = " ".join(str(tuple(r)) for r in plan)
    # SQLite reports a covering index as "USING COVERING INDEX" — a stronger
    # result than a plain "USING INDEX" (no table lookup needed at all), so
    # accept either. What must never appear is a full table scan.
    assert "INDEX" in text and "idx_parts_cat_active_price" in text, text
    assert "SCAN parts" not in text, text
