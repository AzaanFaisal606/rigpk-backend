"""
Regression tests for the round-1 wedge fix (see backend/deps.py's module
docstring and .superpowers/sdd/2026-08-14-phase3-backend-api/task-2-report.md).

The original `get_database()` ran a per-request `SELECT 1` health probe on
the owner thread, marshaled via a bare `.result()` with no timeout, while
holding the module-level `_lock`. A single stalled call to that probe (or to
any DB call marshaled the same way) blocked forever, and every other
request's dependency resolution wedged behind the same lock trying to
acquire a `Database` at all.

These tests exercise `ThreadSafeDatabase` directly against a real seeded
sqlite file (no network, `seeded_db`/`client` come from tests/api/conftest.py
which explicitly clears TURSO_* — see conftest and the repo-wide test
config) so they run entirely offline.
"""
import threading
import time

import pytest

import backend.deps as deps
from backend.deps import DatabaseTimeoutError, ThreadSafeDatabase

# Deliberately tiny, passed as `ThreadSafeDatabase(..., call_timeout=...)`
# rather than sleeping past the real `_CALL_TIMEOUT` (30s in production,
# overridable via API_DB_CALL_TIMEOUT). Sleeping past 30s would make this one
# test take longer than the entire rest of the suite combined; the timeout
# codepath being exercised doesn't care what the actual number is, only that
# a call outlasts whatever timeout the wrapper was given.
_TEST_TIMEOUT = 0.2


class _FakeDB:
    """Stand-in installed via `ThreadSafeDatabase._set_owner_db` to simulate
    failure modes without needing a real hung network call or a live libsql
    connection."""

    def __init__(self):
        self.calls = 0

    def close(self):
        pass

    def hang(self, sleep_seconds):
        # Simulate a stalled network read: sleeps past the caller-supplied
        # timeout, then returns normally (a real wedge would never return --
        # this just needs to outlast the timeout window, not run forever, so
        # the test process itself never hangs).
        time.sleep(sleep_seconds)
        return "should never be observed"

    def flaky_once(self):
        # First call raises a connect-time-shaped error (matches
        # db/libsql_adapter.py's _TRANSIENT_MARKERS: "temporarily
        # unavailable" -- provably before any statement could have reached
        # the server, so safe to retry regardless of what the marshaled
        # call does). Second call (the retry) succeeds.
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporarily unavailable")
        return "ok-after-retry"

    def flaky_once_ambiguous(self):
        # First call raises a marker that's reachable AFTER the server
        # already ran the statement (see db/libsql_adapter.py's
        # _TRANSIENT_READ_ONLY_MARKERS and the F4 fix) -- must NOT be
        # retried here, since this whole marshaled call could be a write.
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("connection reset by peer")
        return "should never be observed"

    def always_bad_sql(self):
        # A real SQL-shaped error (e.g. a constraint violation) -- must
        # never be retried.
        self.calls += 1
        raise ValueError("CHECK constraint failed: price_pkr > 0")


def test_hung_call_times_out_and_does_not_wedge_later_calls(seeded_db):
    """
    The regression test for the wedge: a call that hangs past the wrapper's
    call_timeout must raise DatabaseTimeoutError instead of blocking forever,
    and a subsequent normal call -- from a different thread, same wrapper --
    must still succeed afterwards instead of queuing behind the stuck one.

    Uses a wrapper-local `call_timeout=_TEST_TIMEOUT` override, not the real
    (30s) `_CALL_TIMEOUT` -- see module docstring for why.
    """
    wrapper = ThreadSafeDatabase(seeded_db, call_timeout=_TEST_TIMEOUT)
    wrapper._set_owner_db(_FakeDB())

    result_holder = {}

    def call_hang():
        try:
            wrapper.hang(_TEST_TIMEOUT + 1)
            result_holder["hang"] = "no exception raised"
        except DatabaseTimeoutError as exc:
            result_holder["hang"] = exc

    t = threading.Thread(target=call_hang)
    t.start()
    t.join(timeout=_TEST_TIMEOUT + 3)

    assert isinstance(result_holder.get("hang"), DatabaseTimeoutError)

    # The owner thread that was running .hang() is abandoned (still asleep
    # in the background, harmlessly). A fresh owner thread + a fresh, real
    # Database (reconnected lazily against the same seeded sqlite file)
    # must serve this next call without waiting on the stuck one.
    start = time.monotonic()
    items, total = wrapper.list_parts(category="gpu", limit=5)
    elapsed = time.monotonic() - start

    assert total == 30
    assert len(items) == 5
    assert elapsed < _TEST_TIMEOUT + 1, (
        f"call took {elapsed:.2f}s -- looks like it queued behind the hung call "
        "instead of running on a fresh owner thread"
    )

    wrapper.close()


def test_concurrent_dependency_resolution_not_blocked_by_slow_call(seeded_db):
    """
    get_database()'s job is dependency resolution, not query execution.
    While one call is in flight (slow, not yet hung/timed out), building /
    fetching the wrapper reference via get_database() for other requests
    must stay fast -- it must never block on the in-flight call.
    """
    original = deps._db
    deps._db = None
    try:
        wrapper = deps.get_database()

        def slow_call():
            time.sleep(1.0)
            return "slow-done"

        # Occupy the owner thread with a slow (not hung) call in the background.
        bg = threading.Thread(target=lambda: wrapper._run(slow_call))
        bg.start()
        time.sleep(0.1)  # let it actually start running

        start = time.monotonic()
        again = deps.get_database()
        elapsed = time.monotonic() - start

        assert again is wrapper
        assert elapsed < 0.5, f"get_database() took {elapsed:.2f}s while a call was in flight"

        bg.join(timeout=5)
    finally:
        deps._db = original


def test_transient_error_retries_exactly_once_and_succeeds(seeded_db, monkeypatch):
    """
    A rebuild reconnects for real (`_connect_if_needed` calls `Database(path)`
    again once `._db` is cleared), so to observe "exactly one retry" without
    a live network connection, `Database` itself is patched to keep handing
    back the same fake -- letting the retry land on it too, exactly like a
    real reconnect would land on a real (recovered) connection.
    """
    fake = _FakeDB()
    monkeypatch.setattr(deps, "Database", lambda path: fake)
    wrapper = ThreadSafeDatabase(seeded_db)

    result = wrapper.flaky_once()

    assert result == "ok-after-retry"
    assert fake.calls == 2  # one failed attempt + exactly one retry

    wrapper.close()


def test_sql_error_is_never_retried(seeded_db):
    wrapper = ThreadSafeDatabase(seeded_db)
    fake = _FakeDB()
    wrapper._set_owner_db(fake)

    with pytest.raises(ValueError):
        wrapper.always_bad_sql()

    assert fake.calls == 1  # no retry attempted

    wrapper.close()


def test_ambiguous_marker_is_never_replayed_at_the_outer_layer(seeded_db):
    """
    F4 regression, deps.py side: `ThreadSafeDatabase._run` must not replay
    an ENTIRE marshaled call (which may be a write, e.g. `create_shared_build`
    minting and inserting a new code) on a marker that's reachable after the
    server already ran whatever the call did. "connection reset" used to sit
    in db/libsql_adapter.py's unconditionally-safe `_TRANSIENT_MARKERS`,
    which `_is_transient_error` consults here -- it has since moved to the
    read-only-only tier, so the outer layer must now treat it the same as
    any ordinary, non-retried error: raise immediately, exactly once.
    """
    wrapper = ThreadSafeDatabase(seeded_db)
    fake = _FakeDB()
    wrapper._set_owner_db(fake)

    with pytest.raises(RuntimeError, match="connection reset"):
        wrapper.flaky_once_ambiguous()

    assert fake.calls == 1, (
        "the outer layer replayed a call on an ambiguous marker -- for a "
        "write, that could double-execute it"
    )

    wrapper.close()


def test_abandoned_owner_thread_never_leaks_its_connection_to_new_owner(seeded_db):
    """
    Regression for the two-owner-threads race: `_connect_if_needed` used to
    read/write a single `self._db` instance attribute shared by whichever
    thread happened to be running a task at the time. An old owner thread
    that is still draining its queue after `shutdown(wait=False)` (a queued
    task can start running well after the timeout/rebuild that abandoned
    it) could connect on ITS thread and publish that connection into the
    same slot a brand-new owner thread was about to read — a connection
    escaping the thread that created it (permanent sqlite3.ProgrammingError,
    or silent cross-thread cursor sharing on libsql).

    Exercises `_connect_if_needed` directly on two real OS threads, ordered
    so the "old" thread connects first and the "new" thread connects
    second — exactly the ordering that let the old code's shared `self._db`
    leak the old thread's connection into the new thread's subsequent reads.
    """
    wrapper = ThreadSafeDatabase(seeded_db)

    old_connected = threading.Event()
    result = {}

    def old_owner_thread():
        # Simulates a queued task on an abandoned executor finally getting
        # to run its connect step, after the timeout/rebuild that swapped
        # in a new owner has already happened.
        result["old_db"] = wrapper._connect_if_needed()
        old_connected.set()

    def new_owner_thread():
        old_connected.wait(5)
        result["new_db_1"] = wrapper._connect_if_needed()
        # Read again — must still be THIS thread's own connection, never
        # overwritten by the old (abandoned) thread's connect above.
        result["new_db_2"] = wrapper._connect_if_needed()

    t_old = threading.Thread(target=old_owner_thread)
    t_new = threading.Thread(target=new_owner_thread)
    t_old.start()
    t_old.join(timeout=5)
    t_new.start()
    t_new.join(timeout=5)

    assert result["new_db_1"] is result["new_db_2"], (
        "new owner thread's connection changed between two reads on its "
        "own thread"
    )
    assert result["new_db_1"] is not result["old_db"], (
        "new owner thread observed the abandoned old owner thread's "
        "Database instance"
    )

    wrapper.close()


def test_transient_failure_at_both_layers_stays_within_one_call_timeout(
    tmp_path, monkeypatch
):
    """
    A call that fails transiently on BOTH db/libsql_adapter.py's own inner
    retry loop AND backend/deps.py's outer retry must not cost ~2x
    `call_timeout` end to end. Before this fix, the outer retry restarted a
    FRESH `future.result(timeout=call_timeout)` instead of sharing the
    first attempt's budget, and the inner loop had no idea an outer
    deadline existed at all -- so it ran its full backoff schedule
    regardless of how little time the caller had left, on EVERY outer
    attempt.

    _BASE_BACKOFF is shrunk (not _MAX_ATTEMPTS -- the retry COUNT is real)
    so a full inner-loop exhaustion fits comfortably inside a small
    `call_timeout`, and TWO of them don't -- that's what lets a real
    (never mocked) clock tell "one shared budget" apart from "two
    independent ones" in well under a second, per the instruction to
    avoid a real 30s sleep.
    """
    from db import libsql_adapter

    monkeypatch.setattr(libsql_adapter, "_BASE_BACKOFF", 0.1)
    # One full inner exhaustion sleeps 0.1 + 0.2 + 0.4 = 0.7s (3 gaps
    # between 4 attempts). Budget room for one exhaustion plus a little
    # (so the fixed retry gets SOME room and still has to cut itself
    # short), but nowhere near two full exhaustions (1.4s).
    call_timeout = 1.0

    wrapper = ThreadSafeDatabase(tmp_path / "t.db", call_timeout=call_timeout)

    def always_transient():
        # Matches db/libsql_adapter.py's _TRANSIENT_MARKERS, so both the
        # inner loop (via _retry_transient) and the outer retry (via
        # _is_transient_error) treat it as retryable.
        raise ValueError("connection reset by peer")

    def task():
        return libsql_adapter._retry_transient(always_transient)

    start = time.monotonic()
    with pytest.raises((DatabaseTimeoutError, ValueError)):
        wrapper._run(task)
    elapsed = time.monotonic() - start

    # Comfortably above one exhaustion (~0.7s) plus overhead, comfortably
    # below two independent exhaustions (~1.4s) -- what this must never
    # look like is the outer retry paying for a second full, un-bounded
    # inner exhaustion on top of the first.
    assert elapsed < call_timeout * 1.2, (
        f"took {elapsed:.2f}s for call_timeout={call_timeout}s -- looks "
        "like the outer retry restarted its own budget instead of sharing "
        "one deadline with the inner retry loop"
    )

    wrapper.close()
