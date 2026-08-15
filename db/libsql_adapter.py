"""
libsql (Turso) -> sqlite3 compatibility adapter.

`db/database.py` is written against the stdlib `sqlite3` API. Turso's `libsql`
client is *almost* the same surface, but differs in two ways that matter to this
codebase. This module wraps a libsql connection so the rest of the code keeps
using it exactly as if it were a `sqlite3.Connection` — no SQL and no call site
changes required.

Bridged incompatibilities (verified empirically against a live Turso DB):
  1. Row shape. libsql returns plain `tuple`s with no name access and no
     `row_factory`. `_Row` restores sqlite3.Row-style access — `row[0]`,
     `row["col"]`, `dict(row)`, `row.keys()` — built from `cursor.description`.
  2. Error type. libsql raises builtins.ValueError on constraint violations;
     the app catches `sqlite3.IntegrityError`. Constraint ValueErrors are
     re-raised as `sqlite3.IntegrityError` so existing except-blocks still fire.

Everything else the code relies on is native on libsql and passes straight
through: executemany, RETURNING, cursor.rowcount, `with conn:` transactions,
commit/rollback, cross-connection durability. See docs/DB_migration.md for
the full compatibility matrix.

`executescript` and PRAGMA are NOT safely usable remotely, despite earlier
claims in this docstring. libsql's PRAGMA is a hard SQL_PARSE_ERROR, not a
no-op, and the underlying `executescript` silently abandons every statement
after the one that fails — `db/database.py` used to run schema.sql through
`executescript()`, whose first statement is `PRAGMA journal_mode = WAL`, and
as a result *no* table or index in schema.sql was ever created on Turso.
`_apply_schema()` now applies schema.sql statement-by-statement via
`execute()`, skipping PRAGMAs remotely, specifically to avoid this. Do not
reintroduce a call to `conn.executescript()` against a remote connection.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Iterable, Optional, Sequence

import libsql

# Cold libsql connections occasionally fail their first query with a transient
# network/DNS error (the lookup/connect happens lazily on first use). These fail
# BEFORE the statement reaches the server, so retrying is safe — nothing ran.
#
# `backend/deps.py` also calls into `_is_transient` (via `_is_transient_error`)
# to decide whether to rebuild-and-retry an ENTIRE marshaled call — which may
# be a write (e.g. `upsert_products`). This set must stay restricted to
# failures that provably happened before the statement reached the server;
# anything that could mean "the server ran it and only the response was lost"
# belongs in `_TRANSIENT_READ_ONLY_MARKERS` below instead, never here.
_TRANSIENT_MARKERS = (
    "dns error",
    "failed to lookup",
    "error trying to connect",
    "connection reset",
    "connection refused",
    "timed out",
    "temporarily unavailable",
    "broken pipe",
)

# Failures that only prove the RESPONSE read broke — the request may already
# have reached and been executed by the server. Retrying is safe for a read
# (a SELECT has no side effect to duplicate) but not for a write (retrying an
# INSERT whose ack was merely lost would double-insert). Only ever consulted
# by `_Cursor._run` when the statement being retried is known read-only (see
# `_is_readonly_sql`) — never added to `_TRANSIENT_MARKERS`/`_is_transient`,
# which `backend/deps.py` also uses to gate retrying arbitrary (possibly
# write) calls.
_TRANSIENT_READ_ONLY_MARKERS = (
    # Observed live rebuilding price trends against Turso:
    # `ValueError: Hrana: cursor error: cursor error: error reading a body
    # from connection: unexpected EOF during chunk size line` — mid-stream
    # chunked-encoding corruption while pulling back the (large, streamed)
    # result set of a plain SELECT. Succeeded on an immediate retry. Matched
    # on this distinctive tail rather than the full message: Hrana nests
    # wrappers ("cursor error" appears twice above), so the outer framing
    # isn't stable.
    "unexpected eof during chunk size line",
)
_MAX_ATTEMPTS = 4
_BASE_BACKOFF = 0.6  # seconds; exponential: 0.6, 1.2, 2.4

# A retry loop below has no way to know, on its own, how much of the
# caller's overall time budget is left — `backend/deps.py` wraps a marshaled
# call in its own `_CALL_TIMEOUT` (30s default), and this module's retries
# used to restart their own 4.2s sleep budget with no awareness of that outer
# clock at all. Two independent retry budgets stacked (an inner one here, an
# outer one in deps.py that could ALSO retry once) meant one transient error
# could cost up to ~2x the documented wedge-breaker before a caller ever saw
# a result.
#
# `deadline_scope` lets a caller (only `backend/deps.py` today) publish ONE
# absolute deadline that every retry loop in this module — both `_Cursor._run`
# and `_retry_transient` — checks before sleeping for another attempt, so
# the retries here spend from the SAME budget the caller is bounding its
# `future.result(timeout=...)` by, not a separate one layered on top.
#
# threading.local, not a module-level variable: this module's retry loops
# run wherever the caller's task happens to execute — for `backend/deps.py`
# that is always the single owner thread, but nothing here should assume
# only one thread ever calls in. A bare module attribute would let two
# concurrent owner threads (from two independent ThreadSafeDatabase
# instances, e.g. in tests) stomp each other's deadline.
_deadline_local = threading.local()


class deadline_scope:
    """
    Context manager: publish an absolute deadline (`time.monotonic() +
    seconds`) that this module's retry loops honor for their remaining
    lifetime, on the CURRENT thread only. Nests correctly (restores the
    previous value, if any, on exit) though nesting isn't expected in
    practice. Outside any scope, `_deadline_remaining` returns None,
    meaning "no bound" — the original unrestricted-retry behavior, still
    used by anything that talks to `db/database.py` directly (scripts,
    tests) rather than through `backend/deps.py`'s marshaling.
    """

    __slots__ = ("_seconds", "_deadline", "_prev")

    def __init__(self, seconds: float):
        self._seconds = max(0.0, seconds)

    def __enter__(self) -> "deadline_scope":
        self._deadline = time.monotonic() + self._seconds
        self._prev = getattr(_deadline_local, "value", None)
        _deadline_local.value = self._deadline
        return self

    def __exit__(self, *exc_info) -> bool:
        _deadline_local.value = self._prev
        return False


def _deadline_remaining() -> Optional[float]:
    """Seconds left on the current thread's published deadline, or None if
    no `deadline_scope` is active (unrestricted retries)."""
    deadline = getattr(_deadline_local, "value", None)
    if deadline is None:
        return None
    return deadline - time.monotonic()


def _bounded_backoff(default: float) -> Optional[float]:
    """
    How long the next retry attempt should sleep, honoring the active
    deadline: `default` when there is no active deadline or plenty of room
    left; less than `default` (down to 0) when the deadline is close; None
    when there is no time left at all and the retry loop must stop instead
    of sleeping and trying again.
    """
    remaining = _deadline_remaining()
    if remaining is None:
        return default
    if remaining <= 0:
        return None
    return min(default, remaining)


def _is_transient(exc: Exception) -> bool:
    m = str(exc).lower()
    return any(k in m for k in _TRANSIENT_MARKERS)


def _is_transient_read_only(exc: Exception) -> bool:
    m = str(exc).lower()
    return any(k in m for k in _TRANSIENT_READ_ONLY_MARKERS)


def _retry_transient(fn):
    """
    Retry `fn()` on an unconditionally-safe transient failure (see
    `_TRANSIENT_MARKERS`), same attempt count and backoff as `_Cursor._run`.
    Used by `LibsqlConnection.commit`/`rollback` — with Task 2's long-lived,
    reused connection, a transient network blip on commit now matters (it
    used to just kill one short-lived per-request connection). Never
    consults the read-only marker tier: a commit finalizes writes, so an
    ambiguous "response reading failed" error is never safe to retry here.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return fn()
        except ValueError as e:
            last_exc = e
            if _is_transient(e) and attempt < _MAX_ATTEMPTS - 1:
                backoff = _bounded_backoff(_BASE_BACKOFF * (2 ** attempt))
                if backoff is None:
                    # Active deadline (see `deadline_scope`) has already
                    # expired — another attempt has no realistic chance to
                    # both run and be waited on by the caller. Surface the
                    # transient error now instead of eating into time the
                    # caller no longer has.
                    raise
                time.sleep(backoff)
                continue
            raise
    raise last_exc  # pragma: no cover - unreachable, mirrors _Cursor._run


def _is_readonly_sql(sql: str) -> bool:
    """
    True for a statement with no possible write side effect. Currently just
    SELECT — the only read statement `db/database.py` issues through
    `execute()` (verified: every other statement type in that module is
    ALTER/CREATE/DROP/INSERT/UPDATE/DELETE/PRAGMA/ANALYZE). Used to gate
    `_TRANSIENT_READ_ONLY_MARKERS` so a write is never retried on an
    ambiguous "response reading failed" error.
    """
    return sql.lstrip().lower().startswith("select")


class _Row:
    """
    sqlite3.Row work-alike. Supports positional access (row[0]), name access
    (row["col"]), the mapping protocol so dict(row) works, iteration over
    values, and len(). Built once per result set from the column names.
    """

    __slots__ = ("_cols", "_idx", "_vals")

    def __init__(self, cols: tuple[str, ...], idx: dict[str, int], vals: Sequence[Any]):
        self._cols = cols
        self._idx = idx
        self._vals = vals

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._vals[self._idx[key]]
        return self._vals[key]  # int or slice

    def keys(self):
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __eq__(self, other):
        if isinstance(other, _Row):
            return self._vals == other._vals
        return NotImplemented

    def __repr__(self):
        return f"_Row({dict(zip(self._cols, self._vals))!r})"


def _map_error(exc: ValueError) -> Optional[Exception]:
    """
    Translate a libsql SQL error into the sqlite3 exception the app expects,
    or None to signal "re-raise the original". Only constraint violations need
    translating today; the app's two `except sqlite3.IntegrityError` blocks
    (duplicate price_log row, shared-build code collision) depend on it.
    """
    msg = str(exc)
    low = msg.lower()
    if "constraint failed" in low or "constraint violation" in low:
        return sqlite3.IntegrityError(msg)
    return None


def _cols_index(description) -> tuple[Optional[tuple[str, ...]], Optional[dict[str, int]]]:
    if not description:
        return None, None
    cols = tuple(d[0] for d in description)
    return cols, {c: i for i, c in enumerate(cols)}


class _Cursor:
    """Wraps a libsql cursor: maps errors on execute, wraps rows on fetch."""

    def __init__(self, cur):
        self._cur = cur

    # --- execution (error-mapped, transient-retried) ---
    def _run(self, fn, *, readonly: bool = False):
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                fn()
                return self
            except ValueError as e:
                last_exc = e
                mapped = _map_error(e)
                if mapped is not None:
                    raise mapped from e  # constraint error — never retry
                transient = _is_transient(e) or (readonly and _is_transient_read_only(e))
                if transient and attempt < _MAX_ATTEMPTS - 1:
                    backoff = _bounded_backoff(_BASE_BACKOFF * (2 ** attempt))
                    if backoff is None:
                        # Active deadline (see `deadline_scope`) has already
                        # expired — stop retrying instead of spending time
                        # the caller no longer has.
                        raise
                    time.sleep(backoff)
                    continue
                raise
        # Unreachable given the branches above (every path returns or raises),
        # but kept explicit so this function's static return type is
        # unconditionally `_Cursor`, never `_Cursor | None`. A bare fall-through
        # here used to return None silently, and every caller then did
        # `self._conn.execute(...).fetchone()` -> AttributeError on NoneType,
        # masking whatever transient error actually exhausted the retries.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("libsql _Cursor._run exhausted retries without capturing an error")

    def execute(self, sql: str, params: Iterable[Any] = ()):
        p = tuple(params) if params else ()
        return self._run(lambda: self._cur.execute(sql, p), readonly=_is_readonly_sql(sql))

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        # Always a write (executemany is bulk INSERT/UPDATE in this codebase)
        # — never eligible for the read-only marker tier.
        seq = [tuple(p) for p in seq_of_params]
        return self._run(lambda: self._cur.executemany(sql, seq))

    # --- fetching (row-wrapped) ---
    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        cols, idx = _cols_index(self._cur.description)
        return _Row(cols, idx, row) if cols else row

    def fetchall(self):
        rows = self._cur.fetchall()
        cols, idx = _cols_index(self._cur.description)
        if not cols:
            return list(rows)
        return [_Row(cols, idx, r) for r in rows]

    def __iter__(self):
        cols, idx = _cols_index(self._cur.description)
        for r in self._cur:
            yield _Row(cols, idx, r) if cols else r

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return getattr(self._cur, "lastrowid", None)

    @property
    def description(self):
        return self._cur.description

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass


class LibsqlConnection:
    """
    sqlite3.Connection work-alike over a libsql remote connection. Only the
    surface db/database.py uses is implemented. `row_factory` is accepted and
    ignored — rows are always _Row instances.
    """

    def __init__(self, url: str, auth_token: Optional[str]):
        self._conn = libsql.connect(url, auth_token=auth_token)
        self.row_factory = None  # accepted for API parity, ignored

    def cursor(self) -> _Cursor:
        return _Cursor(self._conn.cursor())

    def execute(self, sql: str, params: Iterable[Any] = ()) -> _Cursor:
        return self.cursor().execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> _Cursor:
        return self.cursor().executemany(sql, seq_of_params)

    def executescript(self, script: str):
        # Deliberately unsupported, not merely unimplemented: libsql's
        # executescript() silently swallows a mid-script failure (e.g. a
        # PRAGMA, which libsql rejects outright) and abandons every statement
        # after it, with no exception raised — see the module docstring. That
        # bug is exactly what made every table/index in schema.sql silently
        # fail to appear on Turso. Raise loudly instead of repeating it;
        # callers must split the script and use execute() per statement,
        # skipping PRAGMAs remotely (see db/database.py's _apply_schema()).
        raise NotImplementedError(
            "LibsqlConnection.executescript() is unsafe on libSQL (silently "
            "drops statements after a mid-script failure, e.g. any PRAGMA). "
            "Split the script and call execute() per statement instead."
        )

    def commit(self):
        _retry_transient(self._conn.commit)

    def rollback(self):
        if hasattr(self._conn, "rollback"):
            _retry_transient(self._conn.rollback)

    def close(self):
        self._conn.close()

    # `with conn:` — commit on clean exit, roll back on exception, like sqlite3.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def connect(url: str, auth_token: Optional[str]) -> LibsqlConnection:
    """Open a Turso/libsql connection wrapped to look like sqlite3."""
    return LibsqlConnection(url, auth_token)
