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
connection") but is NOT safe. It was tried here and disproven with a
concurrency test: FastAPI resolves each sync `Depends()` callable via its
own separate `run_in_threadpool` dispatch, independent of the one used for
the route handler body. Under concurrent load the dependency that builds
(or fetches) the `Database` and the handler that then calls methods on it
land on *different* worker threads more often than not — in a throwaway
repro, 17 of 20 concurrent requests picked mismatched threads. A
`threading.local` keyed off "whichever thread happens to be running this
callable right now" can't paper over that: the thread that built the
connection isn't reliably the thread that ends up using it.

So instead: the one process-wide `Database` gets exactly one owning thread —
a dedicated single-worker executor — and every call to it, from whichever
FastAPI worker thread happens to invoke it, is marshaled onto that owner
thread and blocks for the result. That keeps the real win (the connection is
built once, not once per request) while staying inside sqlite3's
thread-affinity rule without ever disabling the check that enforces it. The
trade-off: DB calls are serialized behind one thread rather than able to run
on several at once, which is deliberate given the correctness constraints
above; a small fixed pool of owner threads would recover some of that if a
project ever needs it.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.config import DB_PATH
from db.database import Database


class ThreadSafeDatabase:
    """
    Proxies a `Database` that lives on one dedicated background thread, so
    it can be called from any FastAPI worker thread without ever touching
    the underlying sqlite3/libsql connection off its owning thread.
    """

    def __init__(self, path):
        self._path = path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-owner")
        self._db = self._executor.submit(Database, path).result()

    def _run(self, fn, *args, **kwargs):
        return self._executor.submit(fn, *args, **kwargs).result()

    def is_alive(self) -> bool:
        try:
            self._run(lambda: self._db._conn.execute("SELECT 1").fetchone())
            return True
        except Exception:
            return False

    def rebuild(self) -> None:
        """Replace the underlying connection. Raises if the rebuild itself fails."""
        try:
            self._run(self._db.close)
        except Exception:
            pass
        self._db = self._executor.submit(Database, self._path).result()

    def __getattr__(self, name):
        # Reading the attribute off self._db (to get a bound method) is safe
        # from any thread — it's a plain Python attribute lookup, no query
        # runs. Only *calling* it is marshaled onto the owner thread.
        def _call(*args, __name=name, **kwargs):
            return self._run(getattr(self._db, __name), *args, **kwargs)
        return _call

    def close(self):
        try:
            self._run(self._db.close)
        finally:
            self._executor.shutdown(wait=True)


_db: ThreadSafeDatabase | None = None
_lock = threading.Lock()


def get_database() -> ThreadSafeDatabase:
    """
    FastAPI dependency: the one process-wide database, reconnecting if the
    connection died while idle (Turso drops idle connections). One rebuild
    attempt; if that also fails the exception propagates rather than
    retrying forever.
    """
    global _db
    with _lock:
        if _db is None:
            _db = ThreadSafeDatabase(DB_PATH)
            return _db
        if not _db.is_alive():
            _db.rebuild()
        return _db


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
