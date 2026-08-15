"""
Shared FastAPI dependency for the process-wide database connection.

Every route used to do `with get_db(DB_PATH) as db:`, building a fresh
`Database` per request and paying a 552.9 ms TLS handshake to Turso on each
one. The obvious fix is one `Database` for the whole process — but a bare
sqlite3 (or libsql) connection can only be used on the thread that created
it, and `check_same_thread=False` would silence that error without making
concurrent use of one connection safe (two threads racing on one cursor
corrupts state instead of raising).

The naive middle ground — cache one `Database` per FastAPI worker thread in
a `threading.local`, built on first use — looks right ("FastAPI dispatches
sync route handlers to a threadpool, so give each thread its own
connection") but is NOT safe. FastAPI resolves each sync `Depends()`
callable via its own separate `run_in_threadpool` dispatch, independent of
the one used for the route handler body. Under concurrent load the
dependency that builds (or fetches) the `Database` and the handler that then
calls methods on it land on *different* worker threads more often than not.
A `threading.local` keyed off "whichever thread happens to be running this
callable right now" can't paper over that.

So instead: the one process-wide `Database` gets exactly one owning thread —
a dedicated single-worker executor — and every call to it, from whichever
FastAPI worker thread happens to invoke it, is marshaled onto that owner
thread and blocks for the result (bounded by `_CALL_TIMEOUT`, see below).
That keeps the real win (the connection is built once, not once per request)
while staying inside sqlite3's thread-affinity rule without ever disabling
the check that enforces it.

Round-1 fix (the reason this file looks the way it does now): the first cut
of this design ran a `SELECT 1` health probe on the owner thread, marshaled
and awaited, on *every single request*, and did it while holding the
module-level `_lock`. Two bugs from one line:
  - a stalled network call to Turso (no client-side timeout configured in
    `db/libsql_adapter.py`) blocked the probe forever, and every other
    request's dependency resolution wedged behind the same lock trying to
    get a `Database` at all — a permanent hang of the whole API from one
    stuck TCP read.
  - even when it didn't hang, it was a second full network round trip
    (Tokyo primary / Oregon API, 800-1700 ms measured) added to every
    request just to avoid one TLS handshake.

Both are fixed the same way: there is no more per-request probe. Reconnects
happen reactively, inside `ThreadSafeDatabase._run`, only when an actual
call fails or times out — never predictively, and `_lock` is now only ever
held for the cheap construction/swap of the wrapper *reference*, never
across I/O. See `ThreadSafeDatabase._run` and `get_database()` below for the
detail.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeoutError
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI

from backend.config import DB_PATH
from db.database import Database

logger = logging.getLogger(__name__)

# This is a wedge-breaker, not a latency SLO. Its only job is to guarantee a
# marshaled call eventually gives up instead of hanging the owner thread
# forever (see the F1 fix in task-2-report.md) — it is not a target response
# time and callers must not treat a timeout here as "this request was slow."
# Any value low enough that a real, legitimately-slow-but-alive query could
# plausibly hit it converts a slow response into a hard error, which is worse
# than slow: it tears down a working connection and 500s a request that would
# have succeeded if just given a few more seconds.
#
# Round-1 fix used 5.0s, sized off a single measured worst case of ~1.7s
# (Tokyo primary / Oregon API). That measurement was for one settled call in
# isolation. It undercounts real traffic: `GET /api/stats` alone makes six
# sequential round trips (collapsed to one in Phase 3 Task 3, but live today),
# and a cold Turso connection's first query is measurably slower than a warm
# one. 5s left almost no headroom on any single call once those factors are
# accounted for.
#
# 30.0s instead: generous enough that no legitimate query should ever trip
# it, while still failing well short of "indefinite" (the actual bug this
# exists to fix) and well inside any sane caller/proxy timeout upstream.
# (Compare `BaseScraper`'s 90s read deadline: that number is sized for a
# batch scrape that can afford to wait, not an interactive request path —
# 30s sits between the two deliberately.)
#
# Overridable via `API_DB_CALL_TIMEOUT` (seconds) so it can be tuned on
# Render without a code change, read once here at import time. A malformed
# value (non-numeric) raises `ValueError` at import — fail loudly, never
# silently fall back to the default.
_env_call_timeout = os.environ.get("API_DB_CALL_TIMEOUT")
_CALL_TIMEOUT = 30.0 if _env_call_timeout is None else float(_env_call_timeout)


class DatabaseTimeoutError(RuntimeError):
    """
    A database call marshaled onto the owner thread exceeded `_CALL_TIMEOUT`.
    `backend/main.py` maps this to an HTTP 503.

    What a client should assume after seeing this: the write MAY OR MAY NOT
    have applied. The owner thread that was running the call is abandoned,
    not killed — Python cannot force-stop a blocked thread — so it keeps
    running in the background and, if it was a write, may go on to execute
    and commit after the 503 has already been returned. There is no
    way to observe from here whether that happens for any given call.

    `_note_abandoned_owner_finished` narrows this window where it can (the
    abandoned connection is closed, which discards rather than commits a
    transaction still open at that moment) but that only helps if the call
    hadn't already committed by the time its `finally` runs — the common
    successful-retry-eventually-lands case is not affected by it.

    Practical consequence: a 503 from this handler is safe to retry for an
    idempotent read, but retrying a write blind can double-apply it (see
    `db/libsql_adapter.py`'s `_TRANSIENT_READ_ONLY_MARKERS` for the same
    ambiguity at the network layer). A write endpoint that needs an exactly-
    once guarantee across a 503 needs a caller-supplied idempotency key —
    none of the current write endpoints (`create_shared_build` included)
    have one yet.
    """


def _is_transient_error(exc: Exception) -> bool:
    """
    True if `exc` looks like a network-shaped connection failure rather than
    an ordinary SQL error (constraint violation, bad query, etc).

    Deferred import: `db/libsql_adapter.py` does `import libsql`, and per
    `requirements.txt`, libsql is "only needed when TURSO_DATABASE_URL is
    set; local SQLite mode ... does not import this." Importing it at
    module load time here would make it a hard dependency for local-only
    dev, so it's imported lazily, matching the existing local-import
    pattern in `db/database.py`.
    """
    from db.libsql_adapter import _is_transient  # local import: libsql optional offline

    return _is_transient(exc)


def _deadline_scope(seconds: float):
    """
    Publish `seconds` as the active deadline for `db/libsql_adapter.py`'s
    internal transient-retry loops (`_Cursor._run`, `_retry_transient`), on
    whichever thread this is entered on — always the owner thread here,
    since it's only ever used inside a task run via `ThreadSafeDatabase._run`.

    Without this, a single marshaled call could restart its own inner retry
    budget independently of the outer `future.result(timeout=...)` bound —
    see `ThreadSafeDatabase._run` for the full accounting. Sharing one
    deadline means the inner loop can never outlive the outer one.

    Deferred import for the same reason as `_is_transient_error` above, with
    an extra fallback: if `libsql` genuinely isn't installed (pure local
    SQLite dev), there is no retry loop to bound in the first place, so a
    no-op context manager is correct, not just a workaround.
    """
    try:
        from db.libsql_adapter import deadline_scope  # local import: libsql optional offline
    except ImportError:
        from contextlib import nullcontext

        return nullcontext()
    return deadline_scope(seconds)


class ThreadSafeDatabase:
    """
    Proxies a `Database` that lives on one dedicated background thread, so
    it can be called from any FastAPI worker thread without ever touching
    the underlying sqlite3/libsql connection off its owning thread.

    Connection is lazy: `__init__` does no I/O at all (just spins up an idle
    executor). The real `Database(path)` — which for Turso means a network
    connect — is only created the first time a call actually runs, on the
    owner thread, inside `_run`. That keeps object construction (and hence
    `get_database()`'s lock) free of network calls entirely, including on
    the very first request.
    """

    def __init__(self, path, *, call_timeout: float | None = None):
        # `call_timeout` overrides the module-level `_CALL_TIMEOUT` for this
        # instance only — exists so tests can exercise the timeout path
        # without sleeping past the real (now 30s) production value. Read at
        # construction time, not baked in as a default-arg, so it also picks
        # up a monkeypatched `deps._CALL_TIMEOUT` when not explicitly passed.
        self._path = path
        # NOT a shared instance attribute: connection storage below is a
        # threading.local, one slot per real OS thread. See the class
        # docstring for why a shared `self._db` is unsafe even with a lock
        # protecting the read-modify-write — the hazard is a *second* owner
        # thread (an abandoned one, still draining its queue after
        # `ThreadPoolExecutor.shutdown(wait=False)`) publishing a connection
        # it built into the slot the *current* owner reads from. A
        # threading.local has no such shared slot to race on in the first
        # place: each OS thread's `.db` attribute lives in a namespace no
        # other thread can see or write, by Python's guarantee, not by any
        # lock this class remembers to take. That makes it airtight rather
        # than merely narrower than the old race window.
        self._local = threading.local()
        self._swap_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-owner")
        self._call_timeout = _CALL_TIMEOUT if call_timeout is None else call_timeout
        # How many owner threads have been abandoned (timed out, swapped
        # out from under an in-flight call via `_replace_owner`) AND have
        # since finished that in-flight call and been cleaned up. Protected
        # by `_swap_lock`, same as `_executor`. See `_run`'s use of
        # `_note_abandoned_owner_finished` — this is what makes an
        # abandoned owner an accounted-for, bounded leak instead of a
        # silent one (see `abandoned_owner_count`).
        self._abandoned_owner_count = 0

    def _connect_if_needed(self) -> Database:
        # Only ever called from inside a task running ON the owner thread —
        # so `self._local.db` here is that thread's own slot, untouched by
        # whatever any other (including an abandoned) owner thread is doing
        # to ITS OWN `self._local.db`.
        db = getattr(self._local, "db", None)
        if db is None:
            # allow_remote_migrations=False: the API server reads and writes
            # rows, it does not own the schema. Against Turso it must connect
            # without running a single DDL statement — migrating production
            # from a request handler is the drift the guard exists to stop,
            # and refusing to connect at all would be a total outage.
            db = Database(self._path, allow_remote_migrations=False)
            self._local.db = db
        return db

    def _owner_db(self) -> Database | None:
        """
        Return whatever `Database` is currently connected on the owner
        thread, without creating a new connection — or None if the owner
        hasn't connected yet. Marshaled like every real call: `self._local`
        is a genuine per-OS-thread slot, so reading it from the calling
        thread would see the CALLER's own (unrelated, almost always empty)
        slot, never the owner's. Test-only seam; production code never
        needs to peek at the connection object itself.
        """
        with self._swap_lock:
            executor = self._executor
        return executor.submit(lambda: getattr(self._local, "db", None)).result(
            timeout=self._call_timeout
        )

    def _set_owner_db(self, db: Any) -> None:
        """
        Install `db` as the owner thread's connection without a real
        connect — marshaled onto the owner thread for the same reason as
        `_owner_db`. Test-only, for simulating failure modes (a hung call,
        a flaky reconnect) without a live network connection.
        """
        with self._swap_lock:
            executor = self._executor
        executor.submit(lambda: setattr(self._local, "db", db)).result(
            timeout=self._call_timeout
        )

    def _replace_owner(self, *, wait_for_old: bool) -> None:
        """
        Swap in a fresh owner thread + a fresh (lazily-reconnected) Database,
        abandoning whatever the current owner thread was doing. Locked only
        for the reference swap itself — never for the network call that
        makes the new connection (that happens later, lazily, on the new
        owner thread's first real task).

        `wait_for_old=False` is used after a timeout: the old owner thread
        may be stuck forever on a hung network read, so we must not block
        waiting for it — that would just relocate the wedge. `wait_for_old=
        True` is used after a synchronous transient error, where the old
        owner thread's task has already returned (that's how we got the
        exception to react to), so waiting for shutdown is instant.

        No `self._db = None` here (there is no such shared slot anymore):
        the new owner thread starts with an empty `threading.local` slot of
        its own regardless of what the old owner thread's slot holds, so
        there is nothing to reset.
        """
        new_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-owner")
        with self._swap_lock:
            old_executor, self._executor = self._executor, new_executor
        old_executor.shutdown(wait=wait_for_old)

    def _run(self, task: Callable[[], Any]) -> Any:
        # ONE deadline for this whole call, first attempt and retry alike.
        # Previously the retry branch below called `future.result(timeout=
        # self._call_timeout)` again — a FRESH `_call_timeout`, not the time
        # left on the original one. Combined with db/libsql_adapter.py's own
        # inner retry loop (4 attempts, its own backoff) restarting on every
        # attempt too, one transient failure could cost up to ~2x
        # `_call_timeout` end to end before a caller saw any result. Now
        # both this outer retry and every inner retry loop
        # (`_deadline_scope`, consulted by `_Cursor._run`/`_retry_transient`)
        # spend from this single absolute deadline.
        with self._swap_lock:
            executor = self._executor
        deadline = time.monotonic() + self._call_timeout
        bounded_task = self._make_bounded_task(task, deadline, executor)

        future = executor.submit(bounded_task)
        try:
            return future.result(timeout=self._call_timeout)
        except _FutureTimeoutError as exc:
            # Owner thread is stuck (e.g. a hung network read with no
            # client-side timeout — see db/libsql_adapter.py). Abandon it
            # without waiting; it may never return. A fresh owner thread
            # takes over for every subsequent call so one wedged call can't
            # block the rest of the app behind it forever — that permanent
            # hang is exactly the bug this fix exists to close.
            #
            # The old owner thread is NOT interrupted — Python cannot force
            # a blocked thread to stop — so `bounded_task` (still running,
            # somewhere inside `task()`) will eventually finish or raise on
            # its own, whenever the network call it's stuck in returns.
            # `bounded_task`'s own `finally` (see `_make_bounded_task`)
            # notices, once that happens, that `self._executor` has moved
            # on without it and closes/accounts for that connection then —
            # see `_note_abandoned_owner_finished` for exactly what "safe
            # and bounded" means here, including for a write.
            self._replace_owner(wait_for_old=False)
            raise DatabaseTimeoutError(
                f"database call did not complete within {self._call_timeout}s"
            ) from exc
        except Exception as exc:
            if not _is_transient_error(exc):
                raise  # ordinary SQL error (e.g. a constraint violation) — never retried
            # Connection-level failure (dead / idle-dropped connection).
            # Rebuild the owner regardless (so later calls aren't stuck on
            # the same dead connection), but only actually retry THIS call
            # if the shared deadline still has room — otherwise the retry
            # would just be a second, doomed wait past a budget the caller
            # has already been told is exhausted.
            self._replace_owner(wait_for_old=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DatabaseTimeoutError(
                    f"database call did not complete within {self._call_timeout}s "
                    "(no time left for a retry after a transient-error rebuild)"
                ) from exc
            with self._swap_lock:
                executor = self._executor
            retry_task = self._make_bounded_task(task, deadline, executor)
            return executor.submit(retry_task).result(timeout=remaining)

    def __getattr__(self, name: str):
        # Reading the attribute off the connected Database (to get a bound
        # method) has to happen on the owner thread too now, since it may
        # not be connected yet, or the owner thread itself may get swapped
        # out mid-flight by a rebuild — so the whole lookup-and-call is
        # wrapped in one task, run via `_connect_if_needed()` on whichever
        # thread ends up executing it.
        def _call(*args, __name=name, **kwargs):
            def task():
                db = self._connect_if_needed()
                return getattr(db, __name)(*args, **kwargs)

            return self._run(task)

        return _call

    def _close_local_db(self) -> None:
        # Runs ON the owner thread: close (and forget) THIS thread's own
        # slot, never another thread's — closing a sqlite3/libsql
        # connection off the thread that created it is exactly the hazard
        # this whole class exists to avoid.
        db = getattr(self._local, "db", None)
        if db is not None:
            db.close()
            self._local.db = None

    def _make_bounded_task(
        self, task: Callable[[], Any], deadline: float, owner_executor: ThreadPoolExecutor
    ) -> Callable[[], Any]:
        """
        Wrap `task` for submission to `owner_executor`: bound its inner
        retry budget to `deadline` (see `_run`'s docstring comment on
        sharing one deadline), and — new here — detect, in a `finally`
        that always runs once `task()` returns OR raises, whether THIS
        owner was abandoned (swapped out by `_replace_owner`) while it was
        still running. `owner_executor` is captured per-call, not read
        fresh from `self._executor`, precisely so this comparison means
        "was I replaced", not "what is current right now" (those differ
        exactly during the abandonment window this exists to detect).
        """

        def _bounded_task() -> Any:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                with _deadline_scope(remaining):
                    return task()
            finally:
                with self._swap_lock:
                    abandoned = self._executor is not owner_executor
                if abandoned:
                    self._note_abandoned_owner_finished()

        return _bounded_task

    def _note_abandoned_owner_finished(self) -> None:
        """
        Runs ON an abandoned owner thread, strictly after its in-flight
        call has fully returned or raised — i.e. after whatever it was
        doing (including, for a write, its own `commit()`) is already
        over. Nothing else will ever submit more work to this thread
        (`_replace_owner` already moved `self._executor` on), so:

          - close this thread's own connection. Freeing it here, rather
            than never, is what turns "one leaked thread + socket per
            timeout" into a bounded leak — bounded because it is cleaned
            up as soon as the abandoned call stops running, not held
            forever.
          - `close()` on a connection with a transaction still open
            (sqlite3 and libsql both) discards it rather than committing
            it. That's the closest this design can get to "roll back
            instead of commit" for the abandoned call: it only helps if
            `task()` raised (or is otherwise still short of its own
            commit) by the time this runs — if `task()` already returned
            normally, its commit already happened, on this same thread,
            before this `finally` ever got a chance to run, and nothing
            outside that thread can un-commit it. There is no way to
            interrupt code already past that point without genuinely
            killing the thread, which Python does not support — so this
            is a best-effort narrowing of the write-safety window, not a
            guarantee. See `get_database`'s docstring for what a caller
            should assume after a 503.
          - increment and log `abandoned_owner_count()` so this leak is
            observable (an operator can alert on it climbing) instead of
            silent.
        """
        self._close_local_db()
        with self._swap_lock:
            self._abandoned_owner_count += 1
            count = self._abandoned_owner_count
        logger.warning(
            "ThreadSafeDatabase: an abandoned owner thread finished its "
            "in-flight call and has been closed (%d abandoned owner(s) "
            "closed so far on this wrapper)",
            count,
        )

    def abandoned_owner_count(self) -> int:
        """How many abandoned owner threads have finished their in-flight
        call and been closed, over this wrapper's lifetime. Observability
        seam for `_note_abandoned_owner_finished` — an operator (or a
        test) can watch this climb instead of it being a silent leak."""
        with self._swap_lock:
            return self._abandoned_owner_count

    def close(self) -> None:
        with self._swap_lock:
            executor = self._executor
        try:
            executor.submit(self._close_local_db).result(timeout=self._call_timeout)
        except Exception:
            pass
        # wait=False: if the owner thread is (still) stuck on a hung call,
        # shutdown must not block process/app teardown waiting for it.
        executor.shutdown(wait=False)


_db: ThreadSafeDatabase | None = None
_lock = threading.Lock()


def get_database() -> Database:
    """
    FastAPI dependency: the one process-wide database.

    No per-request health check. `_lock` guards only the first-time
    construction of the wrapper object and the read of the module-level
    reference — both cheap, in-process, no I/O — never a network call.
    Reconnection is reactive (see `ThreadSafeDatabase._run`), triggered by
    an actual failed or timed-out call, not predicted ahead of one.

    Annotated `-> Database` (not `ThreadSafeDatabase`) because every call
    site writes `db: Database = Depends(get_database)`; `ThreadSafeDatabase`
    duck-types the same public surface via `__getattr__`.
    """
    global _db
    with _lock:
        if _db is None:
            _db = ThreadSafeDatabase(DB_PATH)
        db = _db
    return db  # type: ignore[return-value]


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    global _db
    with _lock:
        db, _db = _db, None
    if db is not None:
        try:
            db.close()
        except Exception:
            pass
