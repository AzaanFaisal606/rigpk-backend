"""
Database._transact redoes a whole transaction that a transport fault killed.

`db/libsql_adapter.py` will not replay a single statement that failed inside a
`with conn:` — the transaction died with it, so re-running that one statement
on a fresh stream would apply it outside the unit of work its caller wrote.
Its comment names redoing the whole unit of work as the correct response and
leaves that to the caller. Nothing did it, so a lost stream during
`rebuild_price_trends()` failed a whole scrape at the last step, after the
upsert and every sweep had already succeeded.

These tests pin both halves: that an idempotent block is redone, and that a
block which is NOT safe to redo (`record_scrape_run`, which appends) is not
routed through this at all.
"""
import sqlite3

import pytest

from db.database import Database

_STREAM_LOST = ValueError(
    'Hrana: `api error: `status=404 Not Found, body={"error":"stream not found: 5a0d73ea:114bc5f"}``'
)


class _FailingNthExecute:
    """
    Wraps a real sqlite3 connection and raises a transport-shaped ValueError
    on chosen execute() calls, forwarding everything else. Lets a genuine
    transaction fail partway through and be redone against real SQL.
    """

    def __init__(self, wrapped, fail_on=(), exc=_STREAM_LOST, fail_on_sql=None,
                 fail_sql_times=1):
        self._wrapped = wrapped
        self._fail_on = set(fail_on)
        self._exc = exc
        # Matching on SQL text rather than a call index, for blocks whose
        # transaction is preceded by an unpredictable number of reads.
        self._fail_on_sql = fail_on_sql
        self._fail_sql_times = fail_sql_times
        self.executes = 0
        self.enters = 0

    def _maybe_fail(self, sql):
        self.executes += 1
        if self.executes in self._fail_on:
            raise self._exc
        if self._fail_on_sql and self._fail_sql_times and self._fail_on_sql in sql:
            self._fail_sql_times -= 1
            raise self._exc

    def execute(self, sql, params=()):
        self._maybe_fail(sql)
        return self._wrapped.execute(sql, params)

    def executemany(self, sql, seq):
        self._maybe_fail(sql)
        return self._wrapped.executemany(sql, seq)

    def __enter__(self):
        self.enters += 1
        self._wrapped.__enter__()
        return self

    def __exit__(self, *exc):
        return self._wrapped.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _remote(db, fake):
    """Point a local Database at `fake` and mark it remote, so _transact takes
    the retry branch it otherwise skips on SQLite."""
    db._conn = fake
    db._remote = True


def test_a_lost_stream_redoes_the_whole_block(db):
    fake = _FailingNthExecute(db._conn, fail_on={1})
    _remote(db, fake)

    calls = []

    def _work():
        calls.append(1)
        db._conn.execute("INSERT INTO parts (source, source_id, name, category, url) "
                         "VALUES ('s', 'a', 'n', 'gpu', 'u')")
        return "done"

    assert db._transact(_work) == "done"
    assert len(calls) == 2, "the block itself must be re-entered, not just the statement"
    assert fake.enters == 2


def test_the_redone_block_does_not_double_apply(db):
    """
    The point of redoing the block rather than replaying the statement.
    The first attempt's INSERT died with its transaction, so exactly one row
    must exist afterwards.
    """
    fake = _FailingNthExecute(db._conn, fail_on={2})
    _remote(db, fake)

    def _work():
        db._conn.execute("INSERT INTO parts (source, source_id, name, category, url) "
                         "VALUES ('s', 'a', 'n', 'gpu', 'u')")
        db._conn.execute("INSERT INTO parts (source, source_id, name, category, url) "
                         "VALUES ('s', 'b', 'n', 'gpu', 'u')")

    db._transact(_work)
    n = fake._wrapped.execute("SELECT COUNT(*) c FROM parts").fetchone()["c"]
    assert n == 2, f"expected the two rows the block writes, got {n}"


def test_retries_are_bounded(db):
    fake = _FailingNthExecute(db._conn, fail_on=set(range(1, 50)))
    _remote(db, fake)

    def _work():
        db._conn.execute("SELECT 1")

    with pytest.raises(ValueError, match="stream not found"):
        db._transact(_work, attempts=3)
    assert fake.enters == 3


def test_a_non_transient_error_is_not_retried(db):
    fake = _FailingNthExecute(db._conn, fail_on={1},
                              exc=ValueError("Hrana: SQL_PARSE_ERROR: syntax error"))
    _remote(db, fake)

    def _work():
        db._conn.execute("SELECT 1")

    with pytest.raises(ValueError, match="SQL_PARSE_ERROR"):
        db._transact(_work)
    assert fake.enters == 1, "a deterministic error must fail on the first attempt"


def test_local_sqlite_never_retries(db):
    """
    Marker matching is remote-only. On SQLite these errors cannot occur, and
    a stray ValueError from application code must not be silently re-run.
    """
    fake = _FailingNthExecute(db._conn, fail_on={1})
    db._conn = fake          # deliberately NOT marking _remote

    def _work():
        db._conn.execute("SELECT 1")

    with pytest.raises(ValueError, match="stream not found"):
        db._transact(_work)
    assert fake.enters == 1


def test_a_constraint_error_still_surfaces(db):
    """
    sqlite3.IntegrityError subclasses Exception, not ValueError, so it must
    pass straight through — a UNIQUE violation is a real defect, not a blip.
    """
    _remote(db, _FailingNthExecute(db._conn, fail_on=set()))

    def _work():
        db._conn.execute("INSERT INTO parts (id, source, source_id, name, category, url) "
                         "VALUES (1, 's', 'a', 'n', 'gpu', 'u')")
        db._conn.execute("INSERT INTO parts (id, source, source_id, name, category, url) "
                         "VALUES (1, 's', 'b', 'n', 'gpu', 'u')")

    with pytest.raises(sqlite3.IntegrityError):
        db._transact(_work)


def test_rebuild_price_trends_survives_a_lost_stream(db):
    """
    End to end on the block that actually failed: the trends rebuild is
    replace-wholesale, so a redo must leave exactly one set of rows.
    """
    for i in range(4):
        db.upsert_products([{
            "name": f"RTX 5090 Card {i}", "price_pkr": 500000 + i * 1000,
            "url": f"https://example.com/p/c{i}", "category": "gpu",
            "source": "czone", "scraped_at": "2026-08-01T00:00:00Z",
        }])
    before = db.rebuild_price_trends()
    baseline = db._conn.execute("SELECT COUNT(*) c FROM price_trends").fetchone()["c"]

    # Fail the DELETE that opens the rebuild's transaction — matched by SQL
    # text, since the rebuild runs a variable number of SELECTs before it.
    fake = _FailingNthExecute(db._conn, fail_on_sql="DELETE FROM price_trends")
    _remote(db, fake)
    assert db.rebuild_price_trends() == before

    after = fake._wrapped.execute("SELECT COUNT(*) c FROM price_trends").fetchone()["c"]
    assert after == baseline, "a redone rebuild must not stack rows on the previous set"


def test_record_scrape_run_is_not_routed_through_transact(db):
    """
    Deliberately excluded: it APPENDS a row, so redoing it after a commit
    whose acknowledgement was merely lost would log the same run twice. This
    pins the exclusion so a future "make everything retry" sweep has to
    confront it.
    """
    import inspect
    src = inspect.getsource(Database.record_scrape_run)
    assert "_transact" not in src, (
        "record_scrape_run appends and must not be auto-retried — "
        "a redo would double-log the run"
    )
    db.record_scrape_run("czone", started_at="2026-08-01T00:00:00Z",
                         products=1, ok=True)
    assert db._conn.execute("SELECT COUNT(*) c FROM scrape_runs").fetchone()["c"] == 1
