"""
The price-trend rebuild must never write from a short read.

Turso returns a large enough result set as ZERO rows — no exception, no
truncation flag. Measured against the live DB: the rebuild's own source query
returns 60,000 rows at `LIMIT 60000` and 0 at `LIMIT 67100`; the cliff is
payload bytes, not row count (dropping the ~44-byte `specs` column let the
full result through).

`rebuild_price_trends()` DELETEs the whole table before writing what it
computed, so a silently-empty read does not degrade the trends — it destroys
them, and reports success. It did exactly that once: a rebuild against a DB
holding 67,691 price rows returned "0 price-trend rows computed" and emptied
a 727-row table.

The fix reads one scrape date per query and checks each page against a COUNT.
These tests pin the check and, more importantly, that a failed check leaves
the existing table alone.
"""
import pytest

from db.database import Database


def _p(i, price, at, source="czone"):
    return {
        "name": f"RTX 5090 Card {i}", "price_pkr": price,
        "url": f"https://example.com/p/c{i}", "category": "gpu",
        "source": source, "scraped_at": at,
    }


D1 = "2026-08-01T00:00:00Z"
D2 = "2026-08-08T00:00:00Z"


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.upsert_products([_p(i, 500000 + i * 1000, D1) for i in range(5)])
    d.upsert_products([_p(i, 520000 + i * 1000, D2) for i in range(5)])
    yield d
    d.close()


class _TruncatingConn:
    """Drops rows from the trend source query's result, the way an oversized
    Turso response does — silently, with no error."""

    def __init__(self, wrapped, keep=0):
        self._wrapped = wrapped
        self._keep = keep
        self.deletes = 0

    def execute(self, sql, params=()):
        if "DELETE FROM price_trends" in sql:
            self.deletes += 1
        cur = self._wrapped.execute(sql, params)
        if "ROW_NUMBER()" in sql:
            return _ShortCursor(cur, self._keep)
        return cur

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


class _ShortCursor:
    def __init__(self, cur, keep):
        self._cur = cur
        self._keep = keep

    def fetchall(self):
        return self._cur.fetchall()[: self._keep]

    def __getattr__(self, name):
        return getattr(self._cur, name)


def test_a_full_read_rebuilds_normally(db):
    assert db.rebuild_price_trends() > 0


def test_an_empty_read_raises_instead_of_writing_nothing(db):
    db.rebuild_price_trends()
    before = db._conn.execute("SELECT COUNT(*) c FROM price_trends").fetchone()["c"]
    assert before > 0

    real = db._conn
    db._conn = _TruncatingConn(real, keep=0)
    with pytest.raises(RuntimeError, match="short read"):
        db.rebuild_price_trends()

    db._conn = real
    after = db._conn.execute("SELECT COUNT(*) c FROM price_trends").fetchone()["c"]
    assert after == before, "a short read must leave the existing trends intact"


def test_the_delete_never_runs_on_a_short_read(db):
    """
    The check has to fire BEFORE the wipe, not merely fail the run — the
    destructive step is the DELETE, and a rebuild that raises after it has
    already lost the table.
    """
    fake = _TruncatingConn(db._conn, keep=0)
    db._conn = fake
    with pytest.raises(RuntimeError, match="short read"):
        db.rebuild_price_trends()
    assert fake.deletes == 0


def test_a_partial_read_is_caught_too(db):
    """
    Not just the all-or-nothing case: any page shorter than its COUNT means
    rows went missing, and aggregating what survived would publish a trend
    computed from a subset of the basket.
    """
    db._conn = _TruncatingConn(db._conn, keep=2)   # 5 parts per date, 2 returned
    with pytest.raises(RuntimeError, match="returned 2 rows, expected 5"):
        db.rebuild_price_trends()


def test_the_error_names_the_date_that_came_up_short(db):
    db._conn = _TruncatingConn(db._conn, keep=0)
    with pytest.raises(RuntimeError, match="2026-08-01"):
        db.rebuild_price_trends()
