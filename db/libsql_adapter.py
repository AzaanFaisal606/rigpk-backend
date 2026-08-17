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
# be a write (e.g. `upsert_products`). This set — and `_is_transient`, and
# `_retry_transient`'s use of it for commit()/rollback() — GUARANTEES only
# this: every member can ONLY mean "the attempt never reached, or never was
# accepted by, the server" (a DNS/lookup failure, a refused or otherwise
# rejected connection attempt). It must never contain anything that could
# ALSO mean "the server ran the statement and only the acknowledgement was
# lost" — that ambiguity is exactly what `_TRANSIENT_READ_ONLY_MARKERS`
# below exists to isolate, and reachable-after-execution markers used to sit
# here by mistake (see the historical note there) until an audit found that
# `_Cursor.executemany` and `backend/deps.py`'s whole-call replay were both
# retrying writes on them, risking a double-insert / double-mint. Anything
# that could mean "the server ran it and only the response was lost" belongs
# in `_TRANSIENT_READ_ONLY_MARKERS` instead, never here.
_TRANSIENT_MARKERS = (
    "dns error",
    "failed to lookup",
    "error trying to connect",
    "connection refused",
    "temporarily unavailable",
    # `ValueError: Hrana: `api error: `status=400 Bad Request, body={"error":
    # "Protocol error: failed to parse http request: invalid token"}``
    #
    # The Hrana endpoint's HTTP layer rejected the request before it ever
    # became a statement, so nothing ran — which is what earns it a place in
    # THIS tier rather than `_TRANSIENT_READ_ONLY_MARKERS`. Given how loudly
    # the comment above warns against guessing at that distinction, it was
    # measured rather than reasoned about: 400 sequential single-row INSERTs
    # of distinct keys against a live Turso scratch table, one of which
    # (n=13) failed with this error. The table then held exactly 399 rows,
    # n=13 absent and every other key present exactly once. The server had
    # not executed it, so replaying it cannot double-apply.
    #
    # Rate measured on the same connection path: 1/150 and 1/400 statements,
    # i.e. ~0.25-0.7%, arriving at random statements with no relation to
    # connection age (a fresh connection fails its 5th statement as readily
    # as its 500th). Reproduced with the bare `libsql` client and no part of
    # this adapter involved, so it is a property of the transport, not of
    # anything here. It is also self-healing: every statement after a failure
    # succeeds on the same connection, so retrying in place is right and
    # reconnecting (the `_STREAM_LOST_MARKERS` treatment) is not.
    #
    # Why it matters at all: per-statement odds this small only become
    # visible when a run issues enough statements. `upsert_products` used to
    # issue ~16,700 per scrape, which at 0.5% is a certainty — that is the
    # error that killed a full pipeline run at the 2h20m mark, and it was
    # misread at the time as a connection degrading with age. Batching cut
    # that to ~60 statements per run; this marker covers the rest.
    "failed to parse http request",
)

# Failures that only prove the RESPONSE read broke — the request may already
# have reached and been executed by the server. Retrying is safe for a read
# (a SELECT has no side effect to duplicate) but not for a write (retrying an
# INSERT whose ack was merely lost would double-insert). Only ever consulted
# by `_Cursor._run` when the statement being retried is known read-only (see
# `_is_readonly_sql`) — never added to `_TRANSIENT_MARKERS`/`_is_transient`,
# which `backend/deps.py` also uses to gate retrying arbitrary (possibly
# write) calls, and never consulted by `_retry_transient` (commit/rollback
# also finalize writes and get no special exemption from this ambiguity).
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
    # Relocated here from `_TRANSIENT_MARKERS` (an audit finding): all three
    # are reachable AFTER the server already executed a statement — a reset
    # or a dropped pipe can arrive while the ack is in flight back to the
    # client, and "timed out" during a response read is the exact same
    # shape as the Hrana EOF case above, just a different underlying cause.
    # None of the three prove the request never reached the server, so none
    # of them belong in the unconditionally-safe tier.
    "connection reset",
    "timed out",
    "broken pipe",
)
# The server has forgotten the Hrana stream this connection was talking over.
# Observed live after the API sat idle: every subsequent request failed with
#   ValueError: Hrana: `api error: `status=404 Not Found,
#   body={"error":"stream not found: 5a0d73ea:ad6418"}``
# and kept failing until the process was restarted — the connection is opened
# once in `LibsqlConnection.__init__` and there was no path that ever opened
# another. On Render's free tier, where the dyno idles between requests, that
# is an outage that outlives the idle period rather than a blip.
#
# This is NOT a member of either tier above, and the distinction matters:
#   * Not `_TRANSIENT_MARKERS` — those are retried on the SAME connection,
#     which for a dead stream fails identically every attempt. Retrying is
#     not the fix; reconnecting is.
#   * Not `_TRANSIENT_READ_ONLY_MARKERS` — same reason.
# A 404 also carries stronger information than either tier: the server has no
# such stream, so it cannot have executed the statement on one. What it does
# NOT tell us is whether an enclosing transaction was mid-flight when the
# stream died — see `_Cursor._run` for why that, not execution ambiguity, is
# what gates the replay.
_STREAM_LOST_MARKERS = (
    "stream not found",
    "stream expired",
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


def _is_stream_lost(exc: Exception) -> bool:
    m = str(exc).lower()
    return any(k in m for k in _STREAM_LOST_MARKERS)


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

    def __init__(self, cur, owner: "Optional[LibsqlConnection]" = None):
        self._cur = cur
        # The connection that minted this cursor. Needed to rebuild a lost
        # Hrana stream — `owner` is None only for the hand-built cursors in
        # the adapter's own unit tests, which get the old raise-through
        # behaviour.
        self._owner = owner
        # The last statement run on this cursor, as (callable, readonly), so a
        # fetch that fails mid-response can re-run it. See `_fetch`.
        self._replay: Optional[tuple[Any, bool]] = None
        # Whether any row has already been handed to the caller from the
        # current result set. Once it has, re-running the statement would
        # restart the result set and hand back rows the caller already saw.
        self._consumed = False

    # --- execution (error-mapped, transient-retried) ---
    def _run(self, fn, *, readonly: bool = False):
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                fn(self._cur)
                self._replay = (fn, readonly)
                self._consumed = False
                return self
            except ValueError as e:
                last_exc = e
                mapped = _map_error(e)
                if mapped is not None:
                    raise mapped from e  # constraint error — never retry
                if _is_stream_lost(e) and self._owner is not None:
                    # Heal the connection unconditionally, even when this
                    # statement itself cannot be replayed: leaving a dead
                    # stream in place is what turned one idle period into an
                    # outage lasting until the next deploy. The next caller
                    # gets a working connection either way.
                    replay = self._owner._recover_stream(self)
                    # Replaying is gated on the statement being read-only AND
                    # no `with conn:` block being open. The transaction check
                    # is the load-bearing half: a stream dying mid-transaction
                    # takes the whole transaction with it, so replaying just
                    # the failed statement on a fresh stream would run it
                    # ALONE, outside the transaction its caller wrote. For
                    # `rebuild_price_trends` — DELETE, then executemany INSERT,
                    # inside one `with` — that would mean re-inserting every
                    # trend row on top of the ones the lost DELETE never
                    # removed.
                    if replay and readonly and attempt < _MAX_ATTEMPTS - 1:
                        continue
                    raise
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
        # `_run` passes the CURRENT raw cursor in rather than the closure
        # capturing `self._cur` — a stream recovery swaps that attribute, and
        # a captured reference would replay onto the dead cursor.
        p = tuple(params) if params else ()
        return self._run(lambda cur: cur.execute(sql, p), readonly=_is_readonly_sql(sql))

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        # Always a write (executemany is bulk INSERT/UPDATE in this codebase)
        # — never eligible for the read-only marker tier.
        seq = [tuple(p) for p in seq_of_params]
        return self._run(lambda cur: cur.executemany(sql, seq))

    # --- fetching (row-wrapped, transient-retried by re-running) ---
    def _fetch(self, fn):
        """
        Run a fetch, re-running the whole statement if pulling the response
        back fails transiently.

        `_run` only ever covered `execute()`, but for a large streamed result
        set the connection is still being read long after execute() returned —
        and that is where it breaks. Live example, rebuilding price trends
        against Turso over ~68k price_log rows:

            File "db/libsql_adapter.py", in fetchall
              rows = self._cur.fetchall()
            ValueError: Hrana: `cursor error: ... error reading a body from
            connection: unexpected EOF during chunk size line`

        `_TRANSIENT_READ_ONLY_MARKERS` already named that exact failure, and
        `_is_readonly_sql` already classified the statement — but no retry
        wrapped the fetch, so the marker had no effect on the one call that
        actually raises it.

        A fetch cannot be retried on its own: the failed result set is gone,
        so recovery means re-running the SELECT. That is why this is gated on
        `readonly` (a write must never re-execute) and on `_consumed` (once a
        row has been handed out, re-running would replay rows the caller
        already has).
        """
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return fn()
            except ValueError as e:
                last_exc = e
                stream_lost = _is_stream_lost(e) and self._owner is not None
                recoverable = (
                    self._replay is not None
                    and self._replay[1]          # the statement was read-only
                    and not self._consumed
                    and (stream_lost or _is_transient(e) or _is_transient_read_only(e))
                )
                if not recoverable or attempt == _MAX_ATTEMPTS - 1:
                    raise
                if stream_lost:
                    # Reconnecting returns False inside a transaction, where
                    # re-running the statement on a fresh stream would read
                    # outside the transaction the caller believes it is in.
                    if not self._owner._recover_stream(self):
                        raise
                else:
                    backoff = _bounded_backoff(_BASE_BACKOFF * (2 ** attempt))
                    if backoff is None:
                        raise
                    time.sleep(backoff)
                try:
                    self._replay[0](self._cur)   # re-run the SELECT
                except ValueError as re_exc:
                    # The re-run can fail differently from the fetch that
                    # triggered it. Observed live: a body-read EOF re-ran onto
                    # a stream the server had since dropped, and the resulting
                    # "stream not found" escaped this loop entirely — the next
                    # iteration would otherwise have called fn() on a cursor
                    # with no result set behind it. A lost stream is worth one
                    # rebuild-and-retry here; anything else propagates.
                    last_exc = re_exc
                    if not (_is_stream_lost(re_exc) and self._owner is not None):
                        raise
                    if not self._owner._recover_stream(self):
                        raise
                    self._replay[0](self._cur)
        raise last_exc  # pragma: no cover - loop above always returns or raises

    def fetchone(self):
        row = self._fetch(self._cur.fetchone)
        self._consumed = True
        if row is None:
            return None
        cols, idx = _cols_index(self._cur.description)
        return _Row(cols, idx, row) if cols else row

    def fetchall(self):
        rows = self._fetch(self._cur.fetchall)
        self._consumed = True
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
        # Kept so a lost Hrana stream can be rebuilt in place. The token is
        # held only in memory, exactly as it already was inside the libsql
        # connection object, and is never logged or surfaced in an error.
        self._url = url
        self._auth_token = auth_token
        self._conn = libsql.connect(url, auth_token=auth_token)
        # True between `__enter__` and `__exit__`. Gates statement replay
        # after a stream recovery — see `_Cursor._run`.
        self._in_transaction = False
        self.row_factory = None  # accepted for API parity, ignored

    def _recover_stream(self, cursor: "Optional[_Cursor]" = None) -> bool:
        """
        Rebuild the underlying libsql connection after its Hrana stream was
        lost, and point `cursor` at a cursor on the new one.

        Returns whether the caller's statement is safe to replay: True only
        outside a `with conn:` block. Inside one, the transaction died with
        the stream, so the only correct outcome is to surface the error and
        let the caller redo the whole unit of work.

        No lock: `db/database.py` marshals every call onto a single owner
        thread (`ThreadSafeDatabase`), so two threads never reach here on one
        connection. A failure to reconnect is left to propagate — a
        connection that cannot be rebuilt is not a state worth hiding.
        """
        try:
            self._conn.close()
        except Exception:
            pass  # already dead; closing is best-effort cleanup
        self._conn = libsql.connect(self._url, auth_token=self._auth_token)
        if cursor is not None:
            cursor._cur = self._conn.cursor()
        return not self._in_transaction

    def cursor(self) -> _Cursor:
        return _Cursor(self._conn.cursor(), owner=self)

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
        # A lost stream is never replayed here — a commit finalizes writes,
        # and the transaction it would have finalized no longer exists on the
        # server. Reconnect so the process isn't wedged, then let the caller
        # see that its transaction did not land.
        try:
            _retry_transient(self._conn.commit)
        except ValueError as e:
            if _is_stream_lost(e):
                self._recover_stream()
            raise
        finally:
            self._in_transaction = False

    def rollback(self):
        if not hasattr(self._conn, "rollback"):
            self._in_transaction = False
            return
        try:
            _retry_transient(self._conn.rollback)
        except ValueError as e:
            # A stream that no longer exists has already discarded whatever
            # it was holding — the rollback's goal is met. Reconnect and
            # swallow, rather than raising out of cleanup and masking the
            # original exception that triggered the rollback.
            if not _is_stream_lost(e):
                raise
            self._recover_stream()
        finally:
            self._in_transaction = False

    def close(self):
        self._conn.close()

    # `with conn:` — commit on clean exit, roll back on exception, like sqlite3.
    def __enter__(self):
        self._in_transaction = True
        return self

    def __exit__(self, exc_type, exc, tb):
        # commit()/rollback() clear the flag themselves; the finally is here
        # for the case where one of them raises on the way out, so a failed
        # exit can't leave the connection permanently marked in-transaction
        # and block every later stream recovery from replaying.
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self._in_transaction = False
        return False


def connect(url: str, auth_token: Optional[str]) -> LibsqlConnection:
    """Open a Turso/libsql connection wrapped to look like sqlite3."""
    return LibsqlConnection(url, auth_token)
