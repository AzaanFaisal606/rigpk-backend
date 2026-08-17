"""
_Cursor._run retries transient libsql errors up to _MAX_ATTEMPTS, then must
raise — never fall off the end of the loop and return None. A bare
fall-through used to do exactly that, and every caller followed up with
`.fetchone()` on the None result, turning a transient network error into a
confusing `AttributeError: 'NoneType' object has no attribute 'fetchone'`.
"""
import pytest

from db import libsql_adapter
from db.libsql_adapter import _MAX_ATTEMPTS, _Cursor


class _AlwaysTransientCursor:
    """Fakes the underlying libsql cursor: every execute() looks like a
    transient network failure, never a constraint violation."""

    def __init__(self):
        self.calls = 0

    def execute(self, sql, params):
        self.calls += 1
        raise ValueError("connection reset by peer")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(libsql_adapter.time, "sleep", lambda *_: None)


def test_exhausted_retries_raise_not_return_none():
    fake = _AlwaysTransientCursor()
    cur = _Cursor(fake)

    with pytest.raises(ValueError, match="connection reset"):
        cur.execute("SELECT 1")

    assert fake.calls == _MAX_ATTEMPTS


# --- read-only-only marker: a real production failure observed rebuilding
# price trends against Turso. The error only proves the RESPONSE read broke,
# not that the statement never reached the server, so it must only be
# retried for a read (a SELECT) — never for a write, where that ambiguity
# could double-execute an INSERT/UPDATE. ---

_HRANA_EOF_ERROR = (
    "Hrana: cursor error: cursor error: error reading a body from "
    "connection: unexpected EOF during chunk size line"
)


class _FlakyOnceCursor:
    """Fakes a cursor whose first execute() looks like the observed Hrana
    body-read failure, then succeeds."""

    def __init__(self):
        self.calls = 0

    def execute(self, sql, params):
        self.calls += 1
        if self.calls == 1:
            raise ValueError(_HRANA_EOF_ERROR)


class _AlwaysHranaEofCursor:
    """Fakes a cursor whose execute() always looks like the observed Hrana
    body-read failure — used to prove writes never retry on it."""

    def __init__(self):
        self.calls = 0

    def execute(self, sql, params):
        self.calls += 1
        raise ValueError(_HRANA_EOF_ERROR)


def test_read_only_marker_retries_a_select():
    fake = _FlakyOnceCursor()
    cur = _Cursor(fake)

    cur.execute("SELECT id FROM parts")

    assert fake.calls == 2, "a SELECT must retry past the Hrana body-read error"


def test_read_only_marker_not_retried_for_a_write():
    fake = _AlwaysHranaEofCursor()
    cur = _Cursor(fake)

    with pytest.raises(ValueError, match="unexpected EOF"):
        cur.execute("INSERT INTO parts (name) VALUES (?)", ("x",))

    assert fake.calls == 1, (
        "an INSERT must NOT retry on a response-read failure — the statement "
        "may already have executed server-side, and retrying would double-insert"
    )


def test_read_only_marker_never_applies_to_executemany():
    fake = _AlwaysHranaEofCursor()
    cur = _Cursor(fake)

    class _Many:
        calls = 0

        def executemany(self, sql, seq):
            _Many.calls += 1
            raise ValueError(_HRANA_EOF_ERROR)

        def execute(self, sql, params):  # unused, satisfies _Cursor's shape
            raise AssertionError("execute should not be called")

    cur = _Cursor(_Many())
    with pytest.raises(ValueError, match="unexpected EOF"):
        cur.executemany("INSERT INTO parts (name) VALUES (?)", [("x",)])

    assert _Many.calls == 1, "executemany is always a write — never eligible for the read-only tier"


def test_general_is_transient_excludes_the_read_only_marker():
    """
    backend/deps.py calls `_is_transient` (via `_is_transient_error`) to
    decide whether to rebuild-and-retry an ENTIRE marshaled call, which may
    be a write. The Hrana body-read marker must never leak into that check —
    only `_is_transient_read_only`, gated to known-SELECT statements inside
    this module, may match it.
    """
    exc = ValueError(_HRANA_EOF_ERROR)
    assert libsql_adapter._is_transient(exc) is False
    assert libsql_adapter._is_transient_read_only(exc) is True


# --- commit()/rollback() retry on the same unconditionally-safe marker set
# as execute() (never the read-only tier — a commit finalizes writes). ---


def _wrap_fake_conn(fake):
    """Build a LibsqlConnection around a fake underlying connection, without
    going through __init__ (which opens a real network connection)."""
    conn = object.__new__(libsql_adapter.LibsqlConnection)
    conn._conn = fake
    conn._url = "libsql://fake"
    conn._auth_token = None
    conn._in_transaction = False
    conn.row_factory = None
    return conn


class _FlakyOnceConn:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    def commit(self):
        self.commit_calls += 1
        if self.commit_calls == 1:
            # A connect-time-shaped marker (provably before anything could
            # have reached the server) — stays in the unconditional tier,
            # unlike "connection reset"/"timed out"/"broken pipe" (see the
            # F4 tests below), so this still exercises _retry_transient's
            # normal retry path.
            raise ValueError("temporarily unavailable")

    def rollback(self):
        self.rollback_calls += 1
        if self.rollback_calls == 1:
            raise ValueError("temporarily unavailable")


def test_commit_retries_on_transient_marker():
    fake = _FlakyOnceConn()
    conn = _wrap_fake_conn(fake)

    conn.commit()

    assert fake.commit_calls == 2


def test_rollback_retries_on_transient_marker():
    fake = _FlakyOnceConn()
    conn = _wrap_fake_conn(fake)

    conn.rollback()

    assert fake.rollback_calls == 2


class _HranaEofOnCommitConn:
    def __init__(self):
        self.commit_calls = 0

    def commit(self):
        self.commit_calls += 1
        raise ValueError(_HRANA_EOF_ERROR)


def test_commit_does_not_retry_on_read_only_marker():
    fake = _HranaEofOnCommitConn()
    conn = _wrap_fake_conn(fake)

    with pytest.raises(ValueError, match="unexpected EOF"):
        conn.commit()

    assert fake.commit_calls == 1, (
        "commit() finalizes writes — a response-read failure is ambiguous "
        "there too, so it must not be retried"
    )


# --- F4 regression: "connection reset", "timed out" and "broken pipe" are
# reachable AFTER the server already executed a statement (an ack that got
# lost), same ambiguity class as the Hrana body-read marker above — they
# must not live in the unconditionally-safe tier. ---


class _AmbiguousMarkerCursor:
    """Fakes a cursor whose execute() always fails with a connection-drop
    style message reachable after the server already ran the statement."""

    def __init__(self, message="connection reset by peer"):
        self.calls = 0
        self._message = message

    def execute(self, sql, params):
        self.calls += 1
        raise ValueError(self._message)


class _FlakyOnceAmbiguousCursor:
    def __init__(self, message="connection reset by peer"):
        self.calls = 0
        self._message = message

    def execute(self, sql, params):
        self.calls += 1
        if self.calls == 1:
            raise ValueError(self._message)


@pytest.mark.parametrize(
    "message", ["connection reset by peer", "operation timed out", "broken pipe"]
)
def test_ambiguous_marker_retries_a_select(message):
    fake = _FlakyOnceAmbiguousCursor(message)
    cur = _Cursor(fake)

    cur.execute("SELECT id FROM parts")

    assert fake.calls == 2, f"a SELECT must retry past {message!r}"


@pytest.mark.parametrize(
    "message", ["connection reset by peer", "operation timed out", "broken pipe"]
)
def test_ambiguous_marker_not_retried_for_a_write(message):
    fake = _AmbiguousMarkerCursor(message)
    cur = _Cursor(fake)

    with pytest.raises(ValueError):
        cur.execute("INSERT INTO parts (name) VALUES (?)", ("x",))

    assert fake.calls == 1, (
        f"an INSERT must NOT retry on {message!r} — the statement may already "
        "have executed server-side, and retrying would double-insert"
    )


def test_general_is_transient_excludes_the_ambiguous_connection_markers():
    """
    Same property as test_general_is_transient_excludes_the_read_only_marker
    above, for the three markers this fix relocated: backend/deps.py's
    `_is_transient_error` (which gates replaying an ENTIRE marshaled call,
    possibly a write) must never treat these as unconditionally safe.
    """
    for message in ("connection reset by peer", "operation timed out", "broken pipe"):
        exc = ValueError(message)
        assert libsql_adapter._is_transient(exc) is False, message
        assert libsql_adapter._is_transient_read_only(exc) is True, message


class _AmbiguousMarkerOnCommitConn:
    def __init__(self):
        self.commit_calls = 0

    def commit(self):
        self.commit_calls += 1
        raise ValueError("connection reset by peer")


def test_commit_does_not_retry_on_ambiguous_connection_marker():
    """
    commit() finalizes writes, same as the Hrana-marker case above — an
    ambiguous "the ack may have been lost after the server ran it" failure
    must not be retried there either, now that these three markers carry
    that same ambiguity instead of the old (incorrect) "provably before
    the server saw it" classification.
    """
    fake = _AmbiguousMarkerOnCommitConn()
    conn = _wrap_fake_conn(fake)

    with pytest.raises(ValueError, match="connection reset"):
        conn.commit()

    assert fake.commit_calls == 1, (
        "commit() must not retry on a marker that may mean the write "
        "already landed server-side"
    )


# --- lost Hrana stream: the connection is opened once and lives for the
# process, so when the server forgets its stream (observed after the API sat
# idle) every later statement failed identically until a restart. Recovery is
# a reconnect, not a retry — and replaying the failed statement is gated on
# there being no open transaction to have lost. ---

_STREAM_LOST_ERROR = (
    'Hrana: `api error: `status=404 Not Found, '
    'body={"error":"stream not found: 5a0d73ea:ad6418"}``'
)


class _StreamLostOnceCursor:
    """Raw cursor whose first execute() reports a lost stream. A recovered
    connection hands out a FRESH instance, so a replay lands on a cursor that
    succeeds — mirroring the real thing, where the dead stream can never
    succeed again no matter how many times it is retried."""

    def __init__(self, dead: bool):
        self.dead = dead
        self.calls = 0

    def execute(self, sql, params):
        self.calls += 1
        if self.dead:
            raise ValueError(_STREAM_LOST_ERROR)

    def executemany(self, sql, seq):
        self.calls += 1
        if self.dead:
            raise ValueError(_STREAM_LOST_ERROR)


class _ReconnectingConn:
    """Underlying libsql connection: the first cursor is on a dead stream,
    every cursor minted after a reconnect is live."""

    def __init__(self):
        self.reconnects = 0
        self.closed = 0

    def cursor(self):
        return _StreamLostOnceCursor(dead=self.reconnects == 0)

    def close(self):
        self.closed += 1


def _conn_that_reconnects(monkeypatch):
    """A LibsqlConnection whose libsql.connect is faked: the object returned
    the first time serves a dead stream, and reconnecting bumps its counter so
    later cursors are live."""
    fake = _ReconnectingConn()

    def _fake_connect(url, auth_token=None):
        fake.reconnects += 1
        return fake

    monkeypatch.setattr(libsql_adapter.libsql, "connect", _fake_connect)
    conn = _wrap_fake_conn(fake)
    return conn, fake


def test_stream_lost_marker_is_not_in_either_transient_tier():
    """
    Retrying a dead stream on the same connection fails identically every
    attempt — that is why this marker gets its own recovery path instead of
    being added to either retry tier. `_is_transient` in particular is also
    consulted by backend/deps.py to replay whole (possibly write) calls.
    """
    exc = ValueError(_STREAM_LOST_ERROR)
    assert libsql_adapter._is_stream_lost(exc) is True
    assert libsql_adapter._is_transient(exc) is False
    assert libsql_adapter._is_transient_read_only(exc) is False


def test_select_recovers_by_reconnecting_and_replaying(monkeypatch):
    conn, fake = _conn_that_reconnects(monkeypatch)
    cur = conn.cursor()          # cursor on the dead stream

    cur.execute("SELECT 1")      # must not raise

    assert fake.reconnects == 1, "the connection must be rebuilt exactly once"
    assert fake.closed == 1, "the dead connection must be closed first"


def test_write_reconnects_but_does_not_replay(monkeypatch):
    """
    A 404 proves the server never ran the statement, but not that an
    enclosing transaction survived. Writes surface the error; the connection
    is still healed so the next caller isn't stuck with a dead stream.
    """
    conn, fake = _conn_that_reconnects(monkeypatch)
    cur = conn.cursor()

    with pytest.raises(ValueError, match="stream not found"):
        cur.execute("INSERT INTO parts (name) VALUES (?)", ("x",))

    assert fake.reconnects == 1, "the connection must still be healed"


def test_select_inside_a_transaction_does_not_replay(monkeypatch):
    """
    The transaction died with the stream. Replaying even a SELECT would hand
    the caller a row read outside the transaction it believes it is in, and
    would let a `with conn:` block continue past the point where its earlier
    statements were silently discarded.
    """
    conn, fake = _conn_that_reconnects(monkeypatch)

    with pytest.raises(ValueError, match="stream not found"):
        with conn:
            conn.cursor().execute("SELECT 1")

    assert fake.reconnects == 1, "the connection must still be healed"


def test_executemany_reconnects_but_does_not_replay(monkeypatch):
    conn, fake = _conn_that_reconnects(monkeypatch)
    cur = conn.cursor()

    with pytest.raises(ValueError, match="stream not found"):
        cur.executemany("INSERT INTO price_trends VALUES (?)", [(1,), (2,)])

    assert fake.reconnects == 1


class _StreamLostOnCommitConn:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.closed = 0

    def commit(self):
        self.commit_calls += 1
        raise ValueError(_STREAM_LOST_ERROR)

    def rollback(self):
        self.rollback_calls += 1
        raise ValueError(_STREAM_LOST_ERROR)

    def cursor(self):
        return _StreamLostOnceCursor(dead=False)

    def close(self):
        self.closed += 1


def test_commit_reconnects_and_still_raises(monkeypatch):
    """
    The transaction the commit would have finalized no longer exists, so the
    caller MUST learn its writes did not land — but the process must not be
    left holding a dead connection either.
    """
    fake = _StreamLostOnCommitConn()
    monkeypatch.setattr(libsql_adapter.libsql, "connect",
                        lambda url, auth_token=None: fake)
    conn = _wrap_fake_conn(fake)

    with pytest.raises(ValueError, match="stream not found"):
        conn.commit()

    assert fake.commit_calls == 1, "a commit is never replayed"
    assert fake.closed == 1, "the dead connection must still be rebuilt"


def test_rollback_on_lost_stream_is_swallowed(monkeypatch):
    """
    A stream that no longer exists has already discarded what it held, so the
    rollback's goal is met. Raising out of cleanup would mask the original
    exception that caused the rollback.
    """
    fake = _StreamLostOnCommitConn()
    monkeypatch.setattr(libsql_adapter.libsql, "connect",
                        lambda url, auth_token=None: fake)
    conn = _wrap_fake_conn(fake)

    conn.rollback()  # must not raise

    assert fake.closed == 1


def test_failed_exit_still_clears_the_transaction_flag(monkeypatch):
    """
    `_in_transaction` gates every later replay. If a raising commit could
    leave it set, one failed transaction would disable stream recovery for
    the rest of the process's life.
    """
    fake = _StreamLostOnCommitConn()
    monkeypatch.setattr(libsql_adapter.libsql, "connect",
                        lambda url, auth_token=None: fake)
    conn = _wrap_fake_conn(fake)

    with pytest.raises(ValueError, match="stream not found"):
        with conn:
            pass

    assert conn._in_transaction is False


# --- the response-read failure surfaces on fetch, not execute: for a large
# streamed result set the connection is still being read long after execute()
# returned. The read-only marker tier existed for exactly this error and had
# no effect there, because no retry wrapped the fetch. ---


class _EofOnFetchCursor:
    """execute() always succeeds; fetchall() fails the first N times with the
    observed Hrana body-read error, then returns rows."""

    def __init__(self, failures=1):
        self.failures = failures
        self.executes = 0
        self.fetches = 0
        self.description = (("id",),)

    def execute(self, sql, params):
        self.executes += 1

    def executemany(self, sql, seq):
        self.executes += 1

    def fetchall(self):
        self.fetches += 1
        if self.fetches <= self.failures:
            raise ValueError(_HRANA_EOF_ERROR)
        return [(1,)]

    def fetchone(self):
        self.fetches += 1
        if self.fetches <= self.failures:
            raise ValueError(_HRANA_EOF_ERROR)
        return (1,)


def test_fetchall_reruns_the_select_on_a_response_read_failure():
    fake = _EofOnFetchCursor()
    cur = _Cursor(fake)
    cur.execute("SELECT id FROM price_log")

    rows = cur.fetchall()

    assert len(rows) == 1
    assert fake.executes == 2, "the SELECT must be re-run — the result set is gone"
    assert fake.fetches == 2


def test_fetchone_reruns_the_select_on_a_response_read_failure():
    fake = _EofOnFetchCursor()
    cur = _Cursor(fake)
    cur.execute("SELECT id FROM parts WHERE id = 1")

    assert cur.fetchone() is not None
    assert fake.executes == 2


def test_fetch_does_not_rerun_a_write():
    """
    Re-running an INSERT because its response read broke would double-insert
    — the same ambiguity that keeps this marker out of the unconditional tier
    on the execute path.
    """
    fake = _EofOnFetchCursor(failures=_MAX_ATTEMPTS)
    cur = _Cursor(fake)
    cur.execute("INSERT INTO parts (name) VALUES (?) RETURNING id", ("x",))

    with pytest.raises(ValueError, match="unexpected EOF"):
        cur.fetchone()

    assert fake.executes == 1, "a write must never be re-run to satisfy a fetch"


def test_fetch_gives_up_after_max_attempts():
    fake = _EofOnFetchCursor(failures=_MAX_ATTEMPTS)
    cur = _Cursor(fake)
    cur.execute("SELECT id FROM price_log")

    with pytest.raises(ValueError, match="unexpected EOF"):
        cur.fetchall()

    assert fake.fetches == _MAX_ATTEMPTS


def test_fetch_does_not_rerun_once_rows_were_handed_out():
    """
    A second fetch on the same cursor must not restart the result set — the
    caller would receive rows it has already processed.
    """
    fake = _EofOnFetchCursor(failures=0)
    cur = _Cursor(fake)
    cur.execute("SELECT id FROM price_log")
    cur.fetchall()               # consumes the result set

    fake.failures = _MAX_ATTEMPTS
    fake.fetches = 0
    with pytest.raises(ValueError, match="unexpected EOF"):
        cur.fetchall()

    assert fake.executes == 1, "an already-consumed cursor must not re-run its statement"


class _EofThenStreamLostCursor:
    """The exact live sequence: fetchall() fails with the body-read EOF, and
    the re-run of the SELECT that recovery issues then fails with a lost
    stream. Before the replay was guarded, that second error escaped the retry
    loop entirely."""

    def __init__(self):
        self.executes = 0
        self.fetches = 0
        self.description = (("id",),)
        self.recovered = False

    def execute(self, sql, params):
        self.executes += 1
        # execute #1 = the caller's; #2 = the replay, onto the dead stream.
        if self.executes == 2 and not self.recovered:
            raise ValueError(_STREAM_LOST_ERROR)

    def fetchall(self):
        self.fetches += 1
        if self.fetches == 1:
            raise ValueError(_HRANA_EOF_ERROR)
        return [(1,)]

    def cursor(self):
        # Stands in for both the raw connection and the raw cursor, so a
        # reconnect keeps the same call counters.
        return self


def test_a_replay_that_hits_a_lost_stream_recovers_instead_of_escaping(monkeypatch):
    fake = _EofThenStreamLostCursor()
    conn = _wrap_fake_conn(fake)
    # Reconnecting hands back the same fake, now flagged as recovered so its
    # next execute() succeeds — matching a fresh stream on the same server.
    def _fake_connect(url, auth_token=None):
        fake.recovered = True
        return fake
    monkeypatch.setattr(libsql_adapter.libsql, "connect", _fake_connect)

    cur = _Cursor(fake, owner=conn)
    cur.execute("SELECT id FROM price_log")

    rows = cur.fetchall()

    assert len(rows) == 1
    assert fake.executes == 3, "caller's execute, the failed replay, then the replay on a fresh stream"


# --- the Hrana HTTP-parse 400 -------------------------------------------
#
# `status=400 Bad Request, body={"error":"Protocol error: failed to parse
# http request: invalid token"}` — the endpoint's HTTP layer rejected the
# request before it became a statement. Unlike every other marker in this
# file, its placement was settled by measurement, not inference: 400 live
# single-row INSERTs of distinct keys, one of which failed this way, left
# exactly 399 rows with the failed key absent and no key duplicated. It goes
# in the unconditionally-safe tier because the server provably never ran it.

_HRANA_PARSE_ERROR = (
    'Hrana: `api error: `status=400 Bad Request, body={"error":"Protocol '
    'error: failed to parse http request: invalid token"}``'
)


class _ParseErrorOnceCursor:
    def __init__(self):
        self.executes = 0

    def execute(self, sql, params):
        self.executes += 1
        if self.executes == 1:
            raise ValueError(_HRANA_PARSE_ERROR)
        return self

    @property
    def description(self):
        return [("n",)]

    def fetchall(self):
        return [(1,)]


def test_a_write_retries_on_the_http_parse_error():
    """
    The whole point of the tier choice: this must retry a WRITE. A full
    scrape issues ~60 statements against Turso at a measured ~0.5% per
    statement failure rate, so without this an upsert fails roughly one run
    in three.
    """
    fake = _ParseErrorOnceCursor()
    cur = _Cursor(fake)

    cur.execute("INSERT INTO price_log (part_id, price_pkr) VALUES (?, ?)", (1, 2))

    assert fake.executes == 2, "the write must be replayed, not surfaced"


def test_http_parse_error_is_unconditionally_transient():
    """
    backend/deps.py gates replaying an ENTIRE marshaled call (possibly a
    write) on `_is_transient`. This marker is one of the few that may say
    yes there — the server never executed the statement, so a whole-call
    replay cannot double-apply.
    """
    exc = ValueError(_HRANA_PARSE_ERROR)
    assert libsql_adapter._is_transient(exc) is True
    assert libsql_adapter._is_transient_read_only(exc) is False


def test_http_parse_error_is_not_treated_as_a_lost_stream():
    """
    Reconnecting is the wrong response: the fault is per-request and the
    same connection keeps working afterwards (measured — every statement
    after a failure succeeded on that connection). Treating it as a lost
    stream would throw away a healthy connection on every blip.
    """
    assert libsql_adapter._is_stream_lost(ValueError(_HRANA_PARSE_ERROR)) is False


def test_commit_retries_on_the_http_parse_error():
    class _Conn:
        def __init__(self):
            self.commit_calls = 0

        def commit(self):
            self.commit_calls += 1
            if self.commit_calls == 1:
                raise ValueError(_HRANA_PARSE_ERROR)

    fake = _Conn()
    conn = _wrap_fake_conn(fake)
    conn.commit()
    assert fake.commit_calls == 2


def test_a_sql_parse_error_is_not_swept_up_by_the_marker():
    """
    The marker matches the HTTP request parser, not SQL parsing. A genuine
    malformed-SQL error must still surface on the first attempt — retrying
    it four times would only delay a deterministic failure.
    """
    exc = ValueError("Hrana: SQL_PARSE_ERROR: near \"SELCT\": syntax error")
    assert libsql_adapter._is_transient(exc) is False
    assert libsql_adapter._is_transient_read_only(exc) is False
