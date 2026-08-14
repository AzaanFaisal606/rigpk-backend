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
through: executescript, executemany, RETURNING, cursor.rowcount, PRAGMA
(no-op remotely), `with conn:` transactions, commit/rollback, cross-connection
durability. See docs/DB_migration.md for the full compatibility matrix.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any, Iterable, Optional, Sequence

import libsql

# Cold libsql connections occasionally fail their first query with a transient
# network/DNS error (the lookup/connect happens lazily on first use). These fail
# BEFORE the statement reaches the server, so retrying is safe — nothing ran.
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
_MAX_ATTEMPTS = 4
_BASE_BACKOFF = 0.6  # seconds; exponential: 0.6, 1.2, 2.4


def _is_transient(exc: Exception) -> bool:
    m = str(exc).lower()
    return any(k in m for k in _TRANSIENT_MARKERS)


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
    def _run(self, fn):
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
                if _is_transient(e) and attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_BASE_BACKOFF * (2 ** attempt))
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
        return self._run(lambda: self._cur.execute(sql, p))

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
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
        self._conn.executescript(script)
        return self

    def commit(self):
        self._conn.commit()

    def rollback(self):
        if hasattr(self._conn, "rollback"):
            self._conn.rollback()

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
