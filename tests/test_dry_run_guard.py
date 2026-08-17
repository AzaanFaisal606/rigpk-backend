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


def test_with_as_binding_still_suppresses_commit(tmp_path, monkeypatch):
    """
    G6: _NoCommitConnection.__enter__ used to return self._wrapped instead of
    self. Every current call site uses the bare `with self._conn:` form,
    which stayed safe under that bug because __exit__ (called on the proxy,
    not on the real connection) still swallows the implicit success-commit.
    But `with self._conn as c:` hands `c` the *real* connection when
    __enter__ returns self._wrapped, so c.commit() bypasses the no-op
    wrapper entirely and persists for real — a one-line refactor away from
    silently defeating SCRAPE_NO_DB_WRITE, which the production cutover's
    dry-run rehearsals depend on.
    """
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("SCRAPE_NO_DB_WRITE", "1")

    db = Database(db_file)
    with db._conn as c:
        c.execute(
            "INSERT INTO scrape_runs (source, kind, started_at, finished_at, ok) "
            "VALUES (?, ?, ?, ?, ?)",
            ("czone", "parts", "2026-08-15T00:00:00Z", "2026-08-15T00:05:00Z", 0),
        )
        c.commit()
    db.close()

    monkeypatch.delenv("SCRAPE_NO_DB_WRITE")
    fresh = Database(db_file)
    count = fresh._conn.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0]
    fresh.close()
    assert count == 0, "commit() through the `as c:` binding must still be a no-op"


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


def test_a_successful_with_block_does_not_strand_the_wrapped_connection():
    """
    The proxy's success path must still unwind the wrapped connection, not
    just return early.

    Returning early skipped the wrapped __exit__ entirely, so any state that
    connection tracks across a with-block was never cleared. Against Turso
    that stranded LibsqlConnection._in_transaction at True after the first
    dry-run write, which permanently disabled Hrana stream recovery — every
    later read that lost its stream raised instead of reconnecting, and a
    full dry run died on the trend rebuild's first large SELECT.
    """
    from db.database import _NoCommitConnection

    class _Tracking:
        def __init__(self):
            self.in_transaction = False
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self):
            self.in_transaction = True
            return self

        def __exit__(self, *exc):
            self.in_transaction = False
            return False

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1
            self.in_transaction = False

    inner = _Tracking()
    proxy = _NoCommitConnection(inner)

    with proxy:
        pass

    assert inner.in_transaction is False, (
        "the wrapped connection must not stay marked in-transaction after a "
        "clean exit"
    )
    assert inner.commits == 0, "a dry run must never commit"
    assert inner.rollbacks == 1, "the suppressed work must be discarded, not left dangling"
