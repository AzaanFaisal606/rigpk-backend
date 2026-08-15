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
