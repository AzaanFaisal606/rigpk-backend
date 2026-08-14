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

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeoutError
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI

from backend.config import DB_PATH
from db.database import Database

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
    """A database call marshaled onto the owner thread exceeded `_CALL_TIMEOUT`."""


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
        self._db: Database | None = None
        self._swap_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-owner")
        self._call_timeout = _CALL_TIMEOUT if call_timeout is None else call_timeout

    def _connect_if_needed(self) -> Database:
        # Only ever called from inside a task running ON the owner thread.
        if self._db is None:
            self._db = Database(self._path)
        return self._db

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
        """
        new_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-owner")
        with self._swap_lock:
            old_executor, self._executor = self._executor, new_executor
            self._db = None  # reconnect lazily, on the new owner thread, on next use
        old_executor.shutdown(wait=wait_for_old)

    def _run(self, task: Callable[[], Any]) -> Any:
        with self._swap_lock:
            executor = self._executor
        future = executor.submit(task)
        try:
            return future.result(timeout=self._call_timeout)
        except _FutureTimeoutError as exc:
            # Owner thread is stuck (e.g. a hung network read with no
            # client-side timeout — see db/libsql_adapter.py). Abandon it
            # without waiting; it may never return. A fresh owner thread
            # takes over for every subsequent call so one wedged call can't
            # block the rest of the app behind it forever — that permanent
            # hang is exactly the bug this fix exists to close.
            self._replace_owner(wait_for_old=False)
            raise DatabaseTimeoutError(
                f"database call did not complete within {self._call_timeout}s"
            ) from exc
        except Exception as exc:
            if not _is_transient_error(exc):
                raise  # ordinary SQL error (e.g. a constraint violation) — never retried
            # Connection-level failure (dead / idle-dropped connection).
            # Rebuild once and retry this exact call exactly once; if the
            # retry also fails, let that exception propagate.
            self._replace_owner(wait_for_old=True)
            with self._swap_lock:
                executor = self._executor
            return executor.submit(task).result(timeout=self._call_timeout)

    def __getattr__(self, name: str):
        # Reading the attribute off self._db (to get a bound method) has to
        # happen on the owner thread too now, since self._db can be None
        # (not yet connected) or get swapped out mid-flight by a rebuild —
        # so the whole lookup-and-call is wrapped in one task.
        def _call(*args, __name=name, **kwargs):
            def task():
                db = self._connect_if_needed()
                return getattr(db, __name)(*args, **kwargs)

            return self._run(task)

        return _call

    def close(self) -> None:
        with self._swap_lock:
            executor, db = self._executor, self._db
        if db is not None:
            try:
                executor.submit(db.close).result(timeout=self._call_timeout)
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
