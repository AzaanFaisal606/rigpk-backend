"""
PPC database layer — SQLite wrapper.

Public API
----------
get_db(path)          -> Database   open (or create) the DB
db.upsert_products(products)        bulk upsert a scrape run
db.get_latest_prices(category)      latest price per product
db.get_price_history(source_id, source)  all prices for one product
db.close()

Each product dict must match the scraper output schema:
    {
        "name":          str,
        "price_pkr":     int | None,
        "url":           str,
        "category":      str,
        "source":        str,
        "scraped_at":    str,          # ISO 8601
        "thumbnail_url": str | None,   # optional
    }

Swapping to PostgreSQL later: replace sqlite3 with psycopg2, change
AUTOINCREMENT -> SERIAL, remove PRAGMAs, adjust placeholder %s vs ?.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence
import json
from scrapers.spec_extractor import extract_specs
from db.tokenize import MAX_SEARCH_TOKENS, normalize_name, search_tokens  # noqa: F401

# Load repo-root .env (local dev) so TURSO_*/DB_PATH are available. override=False
# so real environment vars (CI GitHub secrets, Render env) always win over .env.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

_SCHEMA = Path(__file__).parent / "schema.sql"
_DEFAULT_DB = Path(os.getenv("DB_PATH", str(Path(__file__).parent.parent / "data" / "ppc.db")))

# Schema/migrations only need to run once per process against a remote Turso DB
# (the schema is a property of the DB, not the connection, and re-running the
# full executescript on every per-request open would add needless round trips).
# Local SQLite keeps applying on every open — it is cheap and offline.
_REMOTE_SCHEMA_APPLIED: set[str] = set()

# Opt-in for running schema/migrations against a remote (Turso) target. See
# _remote_migration_mode() below.
_ALLOW_REMOTE_MIGRATIONS_ENV = "ALLOW_REMOTE_MIGRATIONS"


def _remote_migration_mode(allow: bool | None) -> str:
    """
    Decide what opening a REMOTE target does about schema/migrations.

    Database() used to run _apply_schema()/_migrate() unconditionally on
    every construction, remote or local. That meant any ad hoc script,
    notebook, or REPL session that called Database() with no explicit
    target silently applied schema/column changes to production Turso —
    the exact mechanism behind production's unplanned drift (latest_price,
    delisted_at, idx_parts_cat_active_price all landed outside any intended
    migration window).

    Three outcomes, because there are genuinely three kinds of caller:

    "run"     — allow=True, or ALLOW_REMOTE_MIGRATIONS=1. The caller owns the
                schema: migration scripts (via scripts/migrations/_guard.py,
                which sets the env var only after confirming the target) and
                the scrape orchestrators.
    "skip"    — allow=False. The caller has explicitly declared it does not
                own the schema and must not touch it: the API server, the
                Discord notifier, ad hoc read paths. It connects normally
                and never runs DDL.
    "refuse"  — allow is None and the env var is unset. Nobody has said
                anything, so this is the ad hoc REPL/notebook case the drift
                came from. Raise loudly.

    "skip" is not the same as the silent skip that would be wrong here.
    A caller passing False has stated it doesn't manage the schema, and a
    reader that cannot migrate cannot drift the schema; refusing to start
    would only take the API down for a schema it was never going to change.
    """
    if allow is True:
        return "run"
    if allow is False:
        return "skip"
    return "run" if os.getenv(_ALLOW_REMOTE_MIGRATIONS_ENV) == "1" else "refuse"


def _refuse_remote_migration(target: str) -> None:
    raise RuntimeError(
        f"Refusing to run schema/migrations against remote database "
        f"{target!r} without explicit opt-in — this would apply schema "
        "changes to a live remote target. Pass allow_remote_migrations=True "
        f"to Database()/get_db(), or set {_ALLOW_REMOTE_MIGRATIONS_ENV}=1, to "
        "migrate it; pass allow_remote_migrations=False if this caller only "
        "reads and writes rows and does not manage the schema."
    )


def _strip_sql_line_comments(script: str) -> str:
    """
    Remove `--` line comments from a SQL script.

    Not a general SQL tokenizer: it treats `--` as a comment marker
    unconditionally, with no awareness of quoted string literals. That is
    safe for schema.sql specifically — every string literal in that file
    (strftime format/arg strings, short enum tags like 'parts') was checked
    by hand and none contains `--`. If schema.sql ever grows a string literal
    containing `--`, this needs real quote-tracking; until then the simple
    per-line strip is correct and keeps the splitter easy to audit.
    """
    return "\n".join(line[: line.find("--")] if "--" in line else line
                      for line in script.splitlines())


def _split_sql_statements(script: str) -> list[str]:
    """
    Split a SQL script into individual statements suitable for one
    self._conn.execute() call each.

    Strips `--` line comments first (schema.sql's comments themselves
    contain semicolons and apostrophes — e.g. "it's not safe in this
    executescript()," and "'model' | 'spec'" — so a naive split(";") on the
    raw text mangles statement boundaries) and drops empty statements left
    behind by comment-only lines.
    """
    stripped = _strip_sql_line_comments(script)
    return [s.strip() for s in stripped.split(";") if s.strip()]


def _slug(url: str) -> str:
    """
    Stable per-product id derived from its URL.

    Raises rather than returning a constant when the URL carries no product
    path: parts is keyed on (source, source_id), so a constant id makes every
    product in a run overwrite the same row — a silent catalogue wipe that
    reports as a successful scrape.
    """
    m = re.match(r"https?://[^/]+/(.+)", url or "")
    if not m:
        raise ValueError(f"cannot derive a product id from URL: {url!r}")
    slug = m.group(1).rstrip("/")
    slug = re.sub(r"[^\w-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug[:200]
    if not slug or slug in {"", "/", "product", "index"}:
        raise ValueError(f"cannot derive a product id from URL: {url!r}")
    return slug


def _median(values: Sequence[float]) -> float:
    """Median of a non-empty list."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2


def _trimmed_mean(values: list[int], frac: float = 0.10) -> tuple[float, int]:
    """
    Drop ceil(frac) of items from each end (by value), return (mean, kept_count).
    Caller guarantees len(values) >= 5 so at least one item always survives.
    """
    s = sorted(values)
    k = math.ceil(len(s) * frac)
    trimmed = s[k: len(s) - k] or s  # safety: never empty
    return sum(trimmed) / len(trimmed), len(trimmed)


def _trimmed_band(values: list[int], frac: float = 0.05) -> tuple[int, int]:
    """
    Robust (min, max) band: drop the most extreme listings from each end so a
    mispriced outlier (data-entry error, junk SKU) doesn't blow out the range.
    Buckets large enough to trim-mean (n>=5) always shed at least one item per
    end (ceil), which catches lone outliers in small buckets too; tiny buckets
    (n<5, shown as a median) keep their full range. Returns surviving low/high.
    """
    s = sorted(values)
    if len(s) < 5:
        return s[0], s[-1]
    k = max(1, math.ceil(len(s) * frac))
    kept = s[k: len(s) - k] or s  # safety: never empty
    return kept[0], kept[-1]


def _ratio_index(ratios: list[float], frac: float = 0.05) -> float:
    """
    The period-over-period price relative for a matched basket: a trimmed
    geometric mean (a Jevons elementary index), not a median.

    The median was the original choice and it was too blunt for baskets this
    small. `_median` of a list of ratios returns EXACTLY 1.0 whenever half or
    more of the basket held its price — which, for retailers that reprice a
    couple of SKUs at a time, is most weeks. Measured over the live
    catalogue: median-of-ratios left 38 of 101 multi-point series perfectly
    flat with a median total movement of 0.93%; the trimmed geometric mean
    leaves 27 flat at 2.07%. The 13 series that differ are ones where a real
    subset repriced and the median discarded it, so the chart claimed
    "unchanged" about a group that had moved.

    Geometric, not arithmetic: price relatives compound, so a +10% followed
    by a -10% must return to the start. The same trim as `_trimmed_band`
    (n>=5 only) guards the tail without pretending it can help a 3-item
    basket, where there is no non-extreme element to fall back on.
    """
    s = sorted(ratios)
    if len(s) >= 5:
        k = max(1, math.ceil(len(s) * frac))
        s = s[k: len(s) - k] or s
    return math.exp(sum(math.log(r) for r in s) / len(s))


# Terms that — regardless of category — flag an item as non-PC-part junk.
# Matched case-insensitively against the product name.
_GLOBAL_BLOCKLIST: tuple[str, ...] = (
    "flash drive",
    "usb drive",
    "external hard drive",
    "portable hard drive",
    "external ssd",
    "portable ssd",
    "optical drive",
    "dvd writer",
    "dvd drive",
    "blu-ray",
    "thermal paste",
    "thermal grease",
    "thermal compound",
    "thermal grizzly",
    "non-nand",
    "non nand",
)

# Per-category extra blocklist terms.
_CATEGORY_BLOCKLIST: dict[str, tuple[str, ...]] = {
    "gpu": (
        "card holder",
        "graphics card holder",
        "gpu holder",
        "gpu support",
        "card support",
        "nvlink",
        "sli bridge",
    ),
    "ssd": (
        "enclosure",
        "docking station",
        "microsd",
        "micro sd",
        "sdxc",
        "sdhc",
        "adapter card",
        "pcie adapter",
        "m.2 adapter",
        "nvme adapter",
        "fulfill kit",
    ),
    "cooling": (
        "thermal pad",
        "fan controller",
        "fan hub",
        "rpd grease",
        "thermal grease",
        "thermal paste",
    ),
    "psu": (
        "case with",       # "Case with 300W Power Supply" combos
        "chassis with",    # "Chassis with 300W Power Supply" combos
    ),
    "monitor": (
        "keyboard",        # mechanical keyboards listed under monitors
        "light bar",       # RGB light bars
        "lightbar",
        "mouse pad",
        # NOTE: "webcam" deliberately excluded — live data has real monitors
        # with a built-in Windows Hello webcam in the product name (e.g.
        # "Philips 27E1N5600HE ... with Windows Hello Webcam"); the term
        # would quarantine genuine inventory, not junk.
    ),
    "hdd": (
        "docking station",
        "portable ssd",    # portable/external SSDs are not HDDs
        "external ssd",
        "enclosure",
        "caddy",
    ),
}


# Minimum sane price in PKR per category.
# Items below these thresholds are accessories or price-parse errors, not real parts.
_MIN_PRICE: dict[str, int] = {
    "gpu":         4000,
    "cpu":         8000,
    "ram":         1000,
    "motherboard": 8000,
    "ssd":         2500,
    "psu":         5000,
    "case":        3000,
    "cooling":      500,
    "hdd":         1500,
    "monitor":     5000,
}

# How many of the most recent scrape dates a trend series shows. Scrape dates
# are irregular, so this is "the last N scrapes", not a time window. At ~300px
# of sparkline the points and their hover targets get unusable past a handful.
# Temporary ceiling until the trends page grows a proper range filter.
#
# Raised 5 -> 10. At 5 the page was mostly dead-flat lines and the window was
# the biggest single reason: measured on the live catalogue, 63 of 93 visible
# multi-point series (68%) were perfectly flat inside the last 5 buckets, vs
# 27 of 101 (27%) over full history. The movement is real, it is just older
# than five weeks — the recent tail happens to be quiet. 10 covers every
# bucket currently held (cpu/gpu 10, ram 8) and still leaves ~32px between
# points on a 300px sparkline, so the hover targets stay usable.
_TREND_MAX_DATES = 10


def _blocked_by(name: str, category: str) -> Optional[str]:
    """Return the rule id that rejects `name`, or None if it passes."""
    lower = name.lower()
    for term in _GLOBAL_BLOCKLIST:
        if term in lower:
            return f"blocklist:global:{term}"
    for term in _CATEGORY_BLOCKLIST.get(category, ()):
        if term in lower:
            return f"blocklist:{category}:{term}"
    return None


_VALID_SPEC_KEYS = frozenset({
    "brand", "socket", "vram", "ddr_type", "speed", "chipset",
    "wattage", "rating", "form_factor", "type", "aio_size",
    "fan_size", "interface", "capacity", "model",
})

# M29: `get_filter_options` used to hand the frontend raw exact spec values
# ("16GB", "32GB", ...) which its own UI then grouped into range-looking
# dropdown labels — but a click still filtered on one exact value, so picking
# a "16-32GB" grouping silently dropped everything except whichever single
# value the UI happened to send. `list_parts` below is the fix: it accepts a
# "lo-hiUNIT" bucket value and applies a real BETWEEN predicate.
# `_emit_bucket_label` (below) produces that label shape but, as of fix
# round 2, is not wired into `get_filter_options`'s response — FilterBar.tsx
# renders any key it's given as a real dropdown, so shipping a label with no
# SPEC_LABELS entry and no getParts() allow-list entry would ship a dead
# control. `_parse_bucket` is the only thing that reads the label shape, so
# a change to one format still requires a change to the other whenever
# Phase 4 wires the emit side back up.
_BUCKETED_SPEC_KEYS = frozenset({"capacity"})
_BUCKET_VALUE_RE = re.compile(r'^(\d+(?:\.\d+)?)(GB|TB)$', re.IGNORECASE)
_BUCKET_LABEL_RE = re.compile(r'^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)(GB|TB)$', re.IGNORECASE)


def _parse_bucket(value: str) -> Optional[tuple[float, float]]:
    """"16-32GB" -> (16, 32). None if `value` isn't a range label (a plain
    "16GB" falls through to exact-match filtering, unchanged)."""
    m = _BUCKET_LABEL_RE.match(value)
    if not m:
        return None
    lo, hi = float(m.group(1)), float(m.group(2))
    return (lo, hi) if lo <= hi else None


def _emit_bucket_label(values: list[str]) -> Optional[str]:
    """
    Collapse a category's distinct spec values into one "{lo}-{hi}{unit}"
    range label, e.g. ["16GB", "32GB"] -> "16-32GB". Returns None (caller
    falls back to the raw list) when the values aren't all the same
    "<number><GB|TB>" shape and unit — mixed units (some SSD capacities are
    GB, others TB) can't be compared as one range without a unit-aware
    predicate, which `list_parts`'s CAST/REPLACE doesn't do.
    """
    matches = [_BUCKET_VALUE_RE.match(v) for v in values]
    if not values or any(m is None for m in matches):
        return None
    parsed = [m for m in matches if m is not None]
    units = {m.group(2).upper() for m in parsed}
    if len(units) != 1:
        return None
    nums = [float(m.group(1)) for m in parsed]
    unit = units.pop()
    lo, hi = min(nums), max(nums)
    if lo == hi:
        return None  # only one distinct value — exact match already works
    fmt = lambda n: str(int(n)) if n == int(n) else str(n)
    return f"{fmt(lo)}-{fmt(hi)}{unit}"


def _like_escape(term: str) -> str:
    """
    Neutralise LIKE wildcards in user input.

    Without this a search for "%" matches the entire catalogue and "_" matches
    any single character, so typing punctuation silently changes the query's
    meaning. Paired with ESCAPE '\\' on the LIKE clause.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_CATEGORY_SPEC_KEYS: dict[str, list[str]] = {
    "cpu":         ["brand", "socket", "model"],
    "gpu":         ["brand", "vram", "model"],
    "ram":         ["brand", "ddr_type", "speed", "capacity"],
    "motherboard": ["brand", "socket", "chipset", "form_factor"],
    "psu":         ["brand", "wattage", "rating"],
    "case":        ["brand", "form_factor"],
    "cooling":     ["brand", "type", "aio_size", "fan_size"],
    "ssd":         ["brand", "interface", "capacity"],
    "hdd":         ["brand"],
    "monitor":     ["brand"],
}


class _NoCommitConnection:
    """
    Wraps a DB connection so commit() is a no-op and everything else passes
    through. Used by the SCRAPE_NO_DB_WRITE dry run: the scrape executes fully
    (fetch, parse, counts, logs, anomaly detection) but nothing persists —
    uncommitted work rolls back on close().

    A proxy rather than attribute assignment because sqlite3.Connection.commit
    is read-only; assigning over it worked on libSQL and crashed on SQLite.
    """

    def __init__(self, conn):
        self._wrapped = conn

    def commit(self, *_args, **_kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def __setattr__(self, name, value):
        if name == "_wrapped":
            object.__setattr__(self, name, value)
        else:
            setattr(self._wrapped, name, value)

    def __enter__(self):
        # Must return self, not self._wrapped: the latter hands a bare
        # `with self._conn as c:` the real connection, and c.commit() would
        # bypass the no-op wrapper entirely — a refactor from the bare
        # `with self._conn:` form (safe today, since __exit__ below already
        # swallows the implicit success-commit) to the `as c:` form would
        # silently make SCRAPE_NO_DB_WRITE inert. Returning self keeps every
        # write inside the with-block going through this proxy's commit().
        self._wrapped.__enter__()
        return self

    def __exit__(self, *exc):
        # `with self._conn:` commits on success in sqlite3. Swallow the success
        # path so a context-managed write is suppressed too; still roll back on
        # error so an exception behaves normally.
        if exc[0] is None:
            return False
        return self._wrapped.__exit__(*exc)


class Database:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        allow_remote_migrations: bool | None = None,
    ):
        # "run" / "skip" / "refuse" — see _remote_migration_mode(). Only
        # consulted when the resolved target is remote.
        self._remote_migration_mode = _remote_migration_mode(allow_remote_migrations)
        url = os.getenv("TURSO_DATABASE_URL")
        if url:
            # Remote Turso (libSQL). `path` is ignored in this mode.
            from db import libsql_adapter  # local import: libsql optional offline
            self._remote = True
            self._target = url
            self._conn = libsql_adapter.connect(url, os.getenv("TURSO_AUTH_TOKEN"))
        else:
            # Local SQLite file.
            self._remote = False
            self._path = Path(path or _DEFAULT_DB)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._target = str(self._path)
            self._conn = sqlite3.connect(str(self._path))
            self._conn.row_factory = sqlite3.Row

        if self._remote and os.getenv("PYTEST_CURRENT_TEST"):
            raise RuntimeError(
                "Refusing to open the remote Turso DB from a test process. "
                "tests/conftest.py strips TURSO_* — if you see this, an import "
                "re-set them."
            )

        self._apply_schema()

        # ── TEMP DRY-RUN SWITCH — remove when scrapers are fixed & verified ──
        # While debugging the curl_cffi / datacenter-block work via manual CI
        # runs, we don't want test scrapes bloating the live Turso DB. With
        # SCRAPE_NO_DB_WRITE=1 the scrape runs completely normally (fetch, parse,
        # counts, logs) but every data commit becomes a no-op, so nothing
        # persists — uncommitted changes roll back on close(). Schema/migrations
        # already committed just above (idempotent no-op on the live DB).
        # Reverting = delete this block + the SCRAPE_NO_DB_WRITE env in scrape.yml.
        # See CLAUDE.md "## TEMP — Dry-run switch".
        if os.getenv("SCRAPE_NO_DB_WRITE") == "1":
            self._conn = _NoCommitConnection(self._conn)

    def _apply_schema(self):
        # Remote DB: apply schema + migrations once per process (see module note).
        if self._remote and self._target in _REMOTE_SCHEMA_APPLIED:
            return
        if self._remote:
            if self._remote_migration_mode == "refuse":
                _refuse_remote_migration(self._target)
            if self._remote_migration_mode == "skip":
                # The caller declared it does not own the schema. Connect
                # without running a single DDL statement — and do NOT mark
                # the target as applied, so a later opt-in caller in the
                # same process still migrates.
                return
        with open(_SCHEMA, encoding="utf-8") as f:
            script = f.read()
        for stmt in _split_sql_statements(script):
            if self._remote and stmt.upper().startswith("PRAGMA"):
                # libSQL rejects PRAGMA outright (SQL_PARSE_ERROR). Under the old
                # executescript() call that error was swallowed and silently
                # abandoned every statement after it — the entire schema (every
                # CREATE TABLE/INDEX) went missing on Turso with no error raised.
                # Skipping PRAGMAs explicitly, statement by statement, is what
                # lets the rest of the schema actually apply remotely.
                continue
            try:
                self._conn.execute(stmt)
            except Exception as e:
                raise RuntimeError(
                    f"schema.sql statement failed: {stmt[:80]!r}: {e}"
                ) from e
        self._migrate()
        self._conn.commit()
        if self._remote:
            _REMOTE_SCHEMA_APPLIED.add(self._target)

    def _migrate(self):
        """
        Idempotent column adds for DBs created before a column existed.
        schema.sql uses CREATE TABLE IF NOT EXISTS, so it never alters an
        existing table — new columns must be added here.
        """
        for table in ("parts", "prebuilts"):
            cols = {
                r["name"]
                for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "is_active" not in cols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
                )
            if "last_seen_at" not in cols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN last_seen_at TEXT DEFAULT NULL"
                )
            if "name_norm" not in cols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN name_norm TEXT DEFAULT NULL"
                )
            if "delisted_at" not in cols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN delisted_at TEXT DEFAULT NULL"
                )
        parts_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(parts)").fetchall()
        }
        if "latest_price" not in parts_cols:
            self._conn.execute(
                "ALTER TABLE parts ADD COLUMN latest_price INTEGER DEFAULT NULL"
            )
        # Backfill, not just add: list_parts() gates on
        # `latest_price IS NOT NULL`, so a DB that gains the column above
        # (or any row that otherwise ended up with a NULL latest_price)
        # would serve an empty catalogue with HTTP 200 until someone
        # remembered to run the standalone backfill script by hand.
        # NULL-only so this can never fight that script or overwrite a
        # fresher cached value — cheap to run every open when there are no
        # NULLs (the WHERE makes it match zero rows and do no work).
        self._conn.execute(
            """
            UPDATE parts SET latest_price = (
                SELECT price_pkr FROM price_log
                WHERE part_id = parts.id AND price_pkr IS NOT NULL
                ORDER BY scraped_at DESC, id DESC
                LIMIT 1
            )
            WHERE latest_price IS NULL
            """
        )
        run_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(scrape_runs)").fetchall()
        }
        for col in ("before_active", "after_active"):
            if col not in run_cols:
                self._conn.execute(
                    f"ALTER TABLE scrape_runs ADD COLUMN {col} INTEGER DEFAULT NULL"
                )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_parts_active ON parts(is_active)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prebuilts_active ON prebuilts(is_active)"
        )
        # Serves the market page's hot query: filter by category + active,
        # order by price. Covers the ORDER BY so the sort resolves from the
        # index (SQLite can walk an ASC index backwards for DESC, so this one
        # index serves both sort directions — no separate DESC variant).
        # Created here rather than in schema.sql: schema.sql's CREATE TABLE IF
        # NOT EXISTS never re-adds columns to a pre-existing table, and this
        # index references category/is_active/latest_price, all of which are
        # ALTERed onto older DBs above. An index in schema.sql referencing
        # them would run inside the same executescript() as the CREATE TABLE,
        # before those ALTERs ever execute, and fail on such a DB.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_parts_cat_active_price "
            "ON parts(category, is_active, latest_price)"
        )
        trend_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(price_trends)").fetchall()
        }
        if "basket_size" not in trend_cols:
            self._conn.execute(
                "ALTER TABLE price_trends ADD COLUMN basket_size INTEGER NOT NULL DEFAULT 0"
            )
        # One-time cleanup: an earlier revision of deactivate_unseen_* created
        # this as a shared scratch table, which is unsafe under overlapping
        # sweeps (cron + manual dispatch + heal rerun hitting the same DB).
        # Drop it if a prior run of that code left it behind.
        self._conn.execute("DROP TABLE IF EXISTS _sweep_seen_ids")
        self._migrate_quarantine_dedup()

        # Populates sqlite_stat1 so the planner picks the composite index
        # instead of guessing. Cheap on this data size; skip when the stats
        # table already exists so it is not re-run on every connection.
        # Local sqlite3 only: Hrana (libSQL's remote protocol) rejects ANALYZE
        # outright ("SQL not allowed statement: ANALYZE") — running it
        # unconditionally would break every remote Database() construction.
        # Turso's query planner keeps its own statistics server-side, so
        # skipping this there costs nothing.
        if not self._remote:
            has_stats = self._conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='sqlite_stat1'"
            ).fetchone()[0]
            if not has_stats:
                self._conn.execute("ANALYZE")

    def _migrate_quarantine_dedup(self):
        """
        Older DBs may have created quarantined_rows before it was deduped on
        (source, url) — add the missing columns, collapse any rows that
        already violate the new key, then create the unique index. Runs
        every time via _migrate(); each step is a no-op once applied. Doing
        this here (not in schema.sql) matters: schema.sql's CREATE TABLE IF
        NOT EXISTS never fires again on a DB that already has the table, and
        creating the unique index before deduping would raise on any
        pre-existing duplicate (source, url) pair.
        """
        q_cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(quarantined_rows)").fetchall()
        }
        if not q_cols:
            return  # table doesn't exist yet (shouldn't happen post schema.sql)
        had_legacy_ts = "quarantined_at" in q_cols
        if "times_rejected" not in q_cols:
            self._conn.execute(
                "ALTER TABLE quarantined_rows ADD COLUMN times_rejected INTEGER NOT NULL DEFAULT 1"
            )
        for col in ("first_seen_at", "last_seen_at"):
            if col not in q_cols:
                self._conn.execute(f"ALTER TABLE quarantined_rows ADD COLUMN {col} TEXT")
                if had_legacy_ts:
                    self._conn.execute(
                        f"UPDATE quarantined_rows SET {col} = quarantined_at WHERE {col} IS NULL"
                    )
                else:
                    self._conn.execute(
                        f"""UPDATE quarantined_rows SET {col} = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                            WHERE {col} IS NULL"""
                    )

        # Collapse any rows sharing (source, url) from before the dedup existed,
        # so the unique index below doesn't fail on pre-existing duplicates.
        dup_groups = self._conn.execute(
            "SELECT source, url FROM quarantined_rows GROUP BY source, url HAVING COUNT(*) > 1"
        ).fetchall()
        for g in dup_groups:
            rows = self._conn.execute(
                "SELECT * FROM quarantined_rows WHERE source IS ? AND url IS ? ORDER BY id",
                (g["source"], g["url"]),
            ).fetchall()
            keep_id = rows[0]["id"]
            total = sum((r["times_rejected"] or 1) for r in rows)
            first = min(r["first_seen_at"] for r in rows if r["first_seen_at"])
            last = max(r["last_seen_at"] for r in rows if r["last_seen_at"])
            self._conn.execute(
                "UPDATE quarantined_rows SET times_rejected = ?, first_seen_at = ?, last_seen_at = ? WHERE id = ?",
                (total, first, last, keep_id),
            )
            self._conn.executemany(
                "DELETE FROM quarantined_rows WHERE id = ?",
                [(r["id"],) for r in rows[1:]],
            )

        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_quarantine_dedup ON quarantined_rows(source, url)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_quarantine_time ON quarantined_rows(last_seen_at)"
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert_products(self, products: list[dict]) -> int:
        """
        Upsert a list of scraped products and log their prices.
        Returns the number of price_log rows inserted.

        Sets parts.latest_price to this call's price_pkr on both the INSERT
        and ON CONFLICT DO UPDATE paths — it is a cache of the newest
        price_log row, kept in sync here rather than derived at read time.
        A back-dated re-scrape (an older scraped_at run applied after a
        newer one) will overwrite latest_price with the older price; this
        matches how the rest of the pipeline treats back-dated runs (see
        CLAUDE.md "Trends") and is not a bug.
        """
        inserted = 0
        skipped = 0
        seen_ids: dict[str, set[int]] = {}
        quarantined: list[tuple] = []
        cur = self._conn.cursor()
        for p in products:
            if not p.get("category"):
                raise ValueError(f"Product missing category: {p.get('name', '<unknown>')!r}")
            price = p.get("price_pkr")
            if price is None:
                skipped += 1
                continue
            min_price = _MIN_PRICE.get(p["category"])
            if min_price is not None and price < min_price:
                skipped += 1
                quarantined.append((p["source"], p["name"], p["category"], price,
                                    p.get("url"), f"min_price:{p['category']}:{min_price}"))
                continue
            rule = _blocked_by(p["name"], p["category"])
            if rule:
                skipped += 1
                quarantined.append((p["source"], p["name"], p["category"], price,
                                    p.get("url"), rule))
                continue
            try:
                source_id = _slug(p["url"])
            except ValueError as exc:
                skipped += 1
                quarantined.append((p["source"], p["name"], p["category"], price,
                                    p.get("url"), f"bad_url:{exc}"))
                continue
            thumbnail = p.get("thumbnail_url")

            raw_specs = extract_specs(p["name"], p["category"])
            specs_json = json.dumps(raw_specs, ensure_ascii=False) if raw_specs else None

            row = cur.execute(
                """
                INSERT INTO parts (source, source_id, name, category, url, thumbnail_url, specs,
                                   name_norm, is_active, last_seen_at, latest_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    name          = excluded.name,
                    category      = excluded.category,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, parts.thumbnail_url),
                    specs         = excluded.specs,
                    name_norm     = excluded.name_norm,
                    is_active     = 1,
                    delisted_at   = NULL,
                    last_seen_at  = excluded.last_seen_at,
                    latest_price  = excluded.latest_price,
                    updated_at    = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                RETURNING id
                """,
                (p["source"], source_id, p["name"], p["category"], p["url"], thumbnail, specs_json,
                 normalize_name(p["name"]), p["scraped_at"], price),
            ).fetchone()
            part_id = row["id"]
            seen_ids.setdefault(p["source"], set()).add(part_id)

            try:
                cur.execute(
                    """
                    INSERT INTO price_log (part_id, price_pkr, scraped_at)
                    VALUES (?, ?, ?)
                    """,
                    (part_id, p.get("price_pkr"), p["scraped_at"]),
                )
                inserted += 1
            except sqlite3.IntegrityError as exc:
                # UNIQUE (part_id, scraped_at): the same run re-scraped this
                # part. Expected and harmless. Anything else is a real defect
                # and must not be swallowed.
                if "UNIQUE" not in str(exc).upper():
                    raise

        if quarantined:
            # Diagnostic-only: a failure here (e.g. a pre-dedup DB the migration
            # hasn't reached yet) must never abort a real scrape's upsert.
            try:
                cur.executemany(
                    """
                    INSERT INTO quarantined_rows (source, name, category, price_pkr, url, rule)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, url) DO UPDATE SET
                        name           = excluded.name,
                        category       = excluded.category,
                        price_pkr      = excluded.price_pkr,
                        rule           = excluded.rule,
                        times_rejected = quarantined_rows.times_rejected + 1,
                        last_seen_at   = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """,
                    quarantined,
                )
            except Exception as e:
                print(f"    WARNING: quarantine write failed (non-fatal): {e}")

        self._conn.commit()
        self._last_seen_ids = seen_ids
        return inserted

    def list_quarantined(self, limit: int = 100) -> list[dict]:
        """Most recent quarantined rows, newest first. Diagnostic use only."""
        rows = self._conn.execute(
            "SELECT * FROM quarantined_rows ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def deactivate_unseen_parts(self, source: str) -> int:
        """
        Mark every part of `source` that the most recent upsert_products() call
        did NOT see as inactive (is_active = 0). Inactive parts keep their rows
        and full price history — they are only hidden from the site listings,
        and flip back to active if the product reappears in a later scrape.

        Caller must gate this on a successful scrape: a source that returned 0
        products (or raised) must NOT be swept, or a transient block would hide
        its entire catalogue.

        Returns the number of parts newly marked inactive.

        No scratch table: read this source's currently-active ids, subtract
        the seen-id set in Python, and UPDATE the (usually much smaller)
        to-deactivate set in chunks of <=900 ids. Avoids both the
        999-variable ceiling on a single IN(...) and any shared/global
        table name that could collide between overlapping sweeps (weekly
        cron, manual dispatch, self-heal rerun all hit the same remote DB).
        """
        seen = getattr(self, "_last_seen_ids", {}).get(source, set())
        if not seen:
            return 0
        with self._conn:
            active_rows = self._conn.execute(
                "SELECT id FROM parts WHERE source = ? AND is_active = 1",
                (source,),
            ).fetchall()
            to_deactivate = [r["id"] for r in active_rows if r["id"] not in seen]
            if not to_deactivate:
                return 0
            total = 0
            for i in range(0, len(to_deactivate), 900):
                chunk = to_deactivate[i : i + 900]
                placeholders = ",".join("?" * len(chunk))
                cur = self._conn.execute(
                    f"""
                    UPDATE parts SET is_active = 0,
                                     delisted_at = COALESCE(delisted_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                                     updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE is_active = 1
                      AND id IN ({placeholders})
                    """,
                    chunk,
                )
                total += cur.rowcount
        return total

    # ------------------------------------------------------------------
    # Scrape run log
    # ------------------------------------------------------------------

    def record_scrape_run(
        self,
        source: str,
        *,
        kind: str = "parts",
        started_at: str,
        finished_at: str | None = None,
        products: int = 0,
        ok: bool = False,
        swept: int = 0,
        before_active: int | None = None,
        after_active: int | None = None,
        error: str | None = None,
    ) -> None:
        """
        Log the outcome of one source's scrape. Called for failures too — a
        missing row and a failed row mean different things, and the landing
        page's STALE ribbon is driven by the failed ones.

        `before_active` / `after_active` are the source's active-row counts
        either side of the run, for the before/after report. Left NULL when a
        caller doesn't supply them.
        """
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO scrape_runs
                    (source, kind, started_at, finished_at, products, ok, swept,
                     before_active, after_active, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    kind,
                    started_at,
                    finished_at or datetime.now(timezone.utc).isoformat(),
                    products,
                    1 if ok else 0,
                    swept,
                    before_active,
                    after_active,
                    error,
                ),
            )

    def source_health(self, kind: str = "parts") -> dict[str, dict]:
        """
        Latest scrape outcome per source, for the landing-page freshness ribbon.

        `stale` is true when the most recent run for that source failed — its
        listings are the ones from some earlier run and nothing swept them.
        A source with no recorded runs at all is reported as not stale: we have
        no evidence either way, and flagging it would be a guess.
        """
        # Was two round trips (latest-run lookup, then a separate
        # last-success-per-source lookup); last_success_at is now a
        # correlated subquery on the same statement, so this is one.
        latest = self._conn.execute(
            """
            SELECT source, ok, products, finished_at, error,
                   before_active, after_active, swept,
                   (SELECT MAX(finished_at) FROM scrape_runs s2
                    WHERE s2.source = r.source AND s2.kind = r.kind AND s2.ok = 1) AS last_success_at
            FROM scrape_runs r
            WHERE kind = ?
              AND id = (SELECT MAX(id) FROM scrape_runs s
                        WHERE s.source = r.source AND s.kind = r.kind)
            """,
            (kind,),
        ).fetchall()

        return {
            r["source"]: {
                "stale": not r["ok"],
                "last_run_at": r["finished_at"],
                "last_success_at": r["last_success_at"],
                "last_products": r["products"],
                "before_active": r["before_active"],
                "after_active": r["after_active"],
                "last_swept": r["swept"],
                "last_error": r["error"],
            }
            for r in latest
        }

    def create_shared_build(self, build: dict) -> str:
        """
        Create a shared build and return its 6-char alphanumeric code.

        Args:
            build: dict of {slot: {"id": <int>, "qty": <int>,
                "price_at_share": <int|None>}} (e.g.
                {"gpu": {"id": 17, "qty": 1, "price_at_share": 900000}}).
                Stored verbatim as JSON -- this method does no shape
                validation or normalization, that's the caller's job
                (see backend/routers/builds.py). resolve_shared_build()
                also accepts the older bare-int-per-slot shape written by
                codes created before qty/price-snapshot support existed.

        Returns:
            6-char alphanumeric code

        Raises:
            RuntimeError: if all 10 collision retries are exhausted
        """
        build_json = json.dumps(build)
        for _ in range(10):
            code = "".join(secrets.choice(string.ascii_letters + string.digits) for __ in range(6))
            try:
                self._conn.execute(
                    "INSERT INTO shared_builds (code, build_json) VALUES (?, ?)",
                    (code, build_json),
                )
                self._conn.commit()
                return code
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Failed to generate unique shared build code after 10 attempts")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def search_index(self, category: str) -> dict:
        """
        Compact per-category index for the in-browser search.

        Scoped to one category because the market page always is, which keeps
        the payload at 4-35 KB gzipped instead of 176 KB for the whole
        catalogue. Source names are interned; price is included so the client
        can sort and paginate without a round trip.

        Reads parts.latest_price and applies the same `latest_price IS NOT
        NULL` predicate as list_parts() — the client matches, sorts and
        price-filters against this index, then /api/parts?ids=... (list_parts)
        re-fetches exactly those ids. Two different price sources here used to
        let the two disagree (a part visible in search but absent from the
        grid, or a price filter selecting on one number while the grid showed
        another), pinned for up to an hour by this endpoint's ETag cache.
        """
        rows = self._conn.execute(
            """
            SELECT p.id, p.name, p.source, p.latest_price
            FROM parts p
            WHERE p.category = ?
              AND p.is_active = 1
              AND p.latest_price IS NOT NULL
            ORDER BY p.id
            """,
            (category,),
        ).fetchall()

        srcs = sorted({r[2] for r in rows})
        src_idx = {s: i for i, s in enumerate(srcs)}
        payload_rows = [[r[0], r[1], src_idx[r[2]], r[3]] for r in rows]

        # Version = content hash. Drives the ETag, so an unchanged catalogue
        # revalidates to a 304 and a scrape invalidates within the hour.
        digest = hashlib.sha256(
            repr(payload_rows).encode("utf-8")
        ).hexdigest()[:16]

        return {"srcs": srcs, "rows": payload_rows, "version": digest}

    def list_parts(
        self,
        *,
        category: Optional[str] = None,
        source: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        specs_filter: Optional[dict] = None,
        q: Optional[str] = None,
        sort: str = "price_asc",
        limit: int = 50,
        offset: int = 0,
        ids: Optional[list[int]] = None,
        include_specs: bool = False,
    ) -> tuple[list[dict], int]:
        """
        Return (items, total) for the market listing page.
        Items have the latest price per part. NULL-price rows excluded.
        specs_filter: e.g. {"brand": "AMD", "socket": "AM5"}
        include_specs: the market grid never reads `specs` (only /build's
        picker does), so it's left out of the SELECT by default.
        """
        conditions: list[str] = ["p.latest_price IS NOT NULL", "p.is_active = 1"]
        params: list = []

        if category:
            conditions.append("p.category = ?")
            params.append(category)
        if source:
            conditions.append("p.source = ?")
            params.append(source)
        if min_price is not None:
            conditions.append("p.latest_price >= ?")
            params.append(min_price)
        if max_price is not None:
            conditions.append("p.latest_price <= ?")
            params.append(max_price)
        if q:
            # Match the precomputed normalised name. The leading space in both
            # the stored value and the parameter anchors each token at a word
            # start, which is what stops "ti" matching the "ti" in "Edition".
            for token in search_tokens(q):
                conditions.append("p.name_norm LIKE ? ESCAPE '\\'")
                params.append(f"% {_like_escape(token)}%")
        if specs_filter:
            for key, value in specs_filter.items():
                if key not in _VALID_SPEC_KEYS:
                    continue
                bounds = _parse_bucket(value) if key in _BUCKETED_SPEC_KEYS else None
                if bounds:
                    lo, hi = bounds
                    conditions.append(
                        "CAST(REPLACE(REPLACE(json_extract(p.specs, ?), 'GB', ''), 'TB', '') AS REAL) "
                        "BETWEEN ? AND ?"
                    )
                    params.extend([f"$.{key}", lo, hi])
                else:
                    conditions.append("json_extract(p.specs, ?) = ?")
                    params.extend([f"$.{key}", value])

        # `ids` is the client-index path: the browser already matched, filtered,
        # sorted and paged, so we fetch exactly those rows in exactly that order.
        # An empty list means "matched nothing" and must return nothing — it is
        # NOT the same as ids=None, which means "no id filter".
        if ids is not None:
            if not ids:
                return [], 0
            placeholders = ",".join("?" for _ in ids)
            conditions.append(f"p.id IN ({placeholders})")
            params.extend(ids)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        if ids is not None:
            # Preserve the client's order; it already applied the user's sort.
            order = "ORDER BY CASE p.id " + " ".join(
                f"WHEN {int(pid)} THEN {i}" for i, pid in enumerate(ids)
            ) + " END"
            page = ""
            page_params: list = []
        else:
            order = (
                "ORDER BY p.latest_price ASC, p.id ASC" if sort == "price_asc"
                else "ORDER BY p.latest_price DESC, p.id ASC"
            )
            page = "LIMIT ? OFFSET ?"
            page_params = [limit, offset]

        base_query = f"""
            FROM parts p
            {where}
        """

        total = self._conn.execute(
            f"SELECT COUNT(*) {base_query}", params
        ).fetchone()[0]

        specs_col = "p.specs," if include_specs else ""
        rows = self._conn.execute(
            f"""
            SELECT p.id, p.source, p.name, p.category, p.url, p.thumbnail_url,
                   {specs_col} p.last_seen_at, p.latest_price AS price_pkr
            {base_query}
            {order}
            {page}
            """,
            params + page_params,
        ).fetchall()

        return [dict(r) for r in rows], total

    def get_filter_options(self, category: str) -> dict:
        """
        Return distinct spec values present in the DB for a given category.
        Only returns keys that have at least one non-null value.
        e.g. {"brand": ["AMD", "Intel"], "socket": ["AM4", "AM5"]}
        """
        keys = _CATEGORY_SPEC_KEYS.get(category, ["brand"])
        result: dict = {}
        for key in keys:
            json_path = f"$.{key}"
            rows = self._conn.execute(
                """
                SELECT DISTINCT json_extract(specs, ?) AS val
                FROM parts
                WHERE category = ?
                  AND is_active = 1
                  AND latest_price IS NOT NULL
                  AND json_extract(specs, ?) IS NOT NULL
                ORDER BY val
                """,
                (json_path, category, json_path),
            ).fetchall()
            values = [r[0] for r in rows if r[0]]
            if not values:
                continue
            # The raw value list is the shape the deployed frontend's
            # FilterBar.bucketValues() already groups client-side — never
            # replace it.
            result[key] = values
            # Fix round 2: do NOT also emit "<key>_range" here.
            # FilterBar.tsx builds its spec dropdowns generically from
            # Object.entries(filterOptions) — any non-empty key renders as a
            # real dropdown, snake_case label and all, with no SPEC_LABELS
            # entry for "capacity_range" and no allow-list entry in
            # getParts() to carry its value anywhere — so on the live RAM
            # market page this was a dead, mislabeled control with no
            # effect, not an inert additive field. The range predicate
            # itself (_parse_bucket / _BUCKETED_SPEC_KEYS, used by
            # list_parts above) is unaffected and still works if called
            # directly with a "lo-hiUNIT" value. Phase 4 must add the
            # SPEC_LABELS entry and the getParts() allow-list entry in the
            # same change that starts emitting this key again.
        return result

    def get_price_history(self, source_id: str, source: str) -> list[dict]:
        """Return all price log entries for a single product (for graphs)."""
        rows = self._conn.execute(
            """
            SELECT pl.price_pkr, pl.scraped_at
            FROM price_log pl
            JOIN parts p ON p.id = pl.part_id
            WHERE p.source_id = ? AND p.source = ?
            ORDER BY pl.scraped_at
            """,
            (source_id, source),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Price trends (precomputed aggregate per model/spec per scrape date)
    # ------------------------------------------------------------------

    _TREND_MIN_BASKET = 3   # fewer matched parts than this is noise, not a measurement
    # Band (min/max) trim: drop the most extreme 5% each end so mispriced
    # outlier listings don't blow out the displayed range. center_price itself
    # is the matched-basket median/chained level (see rebuild_price_trends);
    # this trim is only for the displayed min/max spread.
    _TREND_BAND_FRAC = 0.05

    # Standard RAM speeds we track (one-off/overclock speeds with few listings
    # are excluded to keep buckets meaningful). Keyed by DDR generation.
    _RAM_STD_SPEEDS = {
        "DDR4": {"2666", "3000", "3200", "3600"},
        "DDR5": {"4800", "5200", "5600", "6000", "6400"},
    }
    # RAM capacities we track (separate buckets per size).
    _RAM_TRACK_CAPS = {"16GB", "32GB"}

    @classmethod
    def _trend_group(cls, category: str, specs: Optional[dict]) -> Optional[tuple[str, str]]:
        """
        Map a part's category + specs to its trend (group_type, group_key),
        or None if the part isn't trendable.
          - gpu/cpu  -> ('model', <model>)              e.g. ('model', 'RTX 4070')
          - ram      -> ('spec',  '<DDR>-<speed>-<cap>') e.g. ('spec', 'DDR4-3200-16GB')

        RAM is gated to standard speeds (_RAM_STD_SPEEDS) and tracked capacities
        (_RAM_TRACK_CAPS, i.e. 16GB/32GB) so each bucket holds genuinely
        comparable kits; 8GB/64GB+ and one-off speeds are dropped.
        """
        if not specs:
            return None
        if category in ("gpu", "cpu"):
            model = specs.get("model")
            return ("model", model) if model else None
        if category == "ram":
            ddr = specs.get("ddr_type")
            speed = specs.get("speed")
            cap = specs.get("capacity")
            if not (ddr and speed and cap):
                return None
            if cap not in cls._RAM_TRACK_CAPS:
                return None
            speed_num = re.sub(r"\D", "", speed)  # "3200MHz" -> "3200"
            if speed_num not in cls._RAM_STD_SPEEDS.get(ddr, ()):
                return None
            return ("spec", f"{ddr}-{speed_num}-{cap}")
        return None

    def rebuild_price_trends(self) -> int:
        """
        Wipe and recompute the entire price_trends table from price_log.
        One row per (category, group_type, group_key, scrape_date). Idempotent.
        Returns the number of trend rows written.
        """
        # One price per (part, calendar date): if a part is scraped twice on the
        # same day (e.g. a retry after a partial run), keep only its latest row
        # so a single listing isn't double-counted in a date bucket.
        rows = self._conn.execute(
            """
            SELECT p.category AS category,
                   p.specs    AS specs,
                   d.scrape_date AS scrape_date,
                   d.price_pkr AS price,
                   d.part_id AS part_id
            FROM (
                SELECT part_id,
                       substr(scraped_at, 1, 10) AS scrape_date,
                       price_pkr,
                       ROW_NUMBER() OVER (
                           PARTITION BY part_id, substr(scraped_at, 1, 10)
                           ORDER BY scraped_at DESC
                       ) AS rn
                FROM price_log
                WHERE price_pkr IS NOT NULL
            ) d
            JOIN parts p ON p.id = d.part_id
            WHERE d.rn = 1
            """
        ).fetchall()

        # Bucket: (category, group_type, group_key, date) -> {part_id: price}
        buckets: dict[tuple[str, str, str, str], dict[int, int]] = {}
        specs_cache: dict[int, Optional[dict]] = {}
        for r in rows:
            pid = r["part_id"]
            if pid not in specs_cache:
                # One json.loads per part, not per price-log row. A part with N
                # snapshots previously re-parsed identical specs JSON N times.
                try:
                    specs_cache[pid] = json.loads(r["specs"]) if r["specs"] else None
                except (json.JSONDecodeError, TypeError):
                    specs_cache[pid] = None
            grp = self._trend_group(r["category"], specs_cache[pid])
            if grp is None:
                continue
            group_type, group_key = grp
            key = (r["category"], group_type, group_key, r["scrape_date"])
            buckets.setdefault(key, {})[pid] = int(r["price"])

        # Regroup by series so consecutive dates can be compared.
        series: dict[tuple[str, str, str], dict[str, dict[int, int]]] = {}
        for (category, group_type, group_key, date), prices in buckets.items():
            series.setdefault((category, group_type, group_key), {})[date] = prices

        records = []
        for (category, group_type, group_key), by_date in series.items():
            dates = sorted(by_date)
            level: Optional[float] = None
            anchored = False
            for i, date in enumerate(dates):
                prices_now = by_date[date]
                if not anchored:
                    # Anchor the chain at the first date whose OWN basket
                    # meets _TREND_MIN_BASKET. The anchor sets the level for
                    # every later point in the chain, so a too-small anchor
                    # (e.g. basket_size=1) undercuts the churn-neutrality the
                    # matched-basket rewrite was built for just as much as a
                    # too-small mid-series point does (handled below) — G7.
                    # A too-small date is skipped, not the whole series
                    # dropped: the group may simply not have enough listings
                    # yet and gain them on a later date.
                    if len(prices_now) < self._TREND_MIN_BASKET:
                        continue
                    basket = prices_now
                    matched = list(basket.values())
                    level = _median(matched)
                    basket_size = len(matched)
                    method = "matched_basket_median"
                    anchored = True
                else:
                    prev = by_date[dates[i - 1]]
                    shared = set(prev) & set(prices_now)
                    basket_size = len(shared)
                    if basket_size < self._TREND_MIN_BASKET:
                        # Not a measurement. Break the chain rather than publish
                        # a number driven by which products happened to appear.
                        level = None
                        continue
                    basket = {p: prices_now[p] for p in shared}
                    if level is None:
                        # Chain was broken earlier — re-anchor on real prices.
                        level = _median(list(basket.values()))
                        method = "matched_basket_median"
                    else:
                        ratio = _ratio_index([prices_now[p] / prev[p] for p in shared],
                                             self._TREND_BAND_FRAC)
                        level = level * ratio
                        method = "matched_basket_chained"

                matched_prices = list(basket.values())
                used = len(matched_prices)
                raw_lo, raw_hi = _trimmed_band(matched_prices, self._TREND_BAND_FRAC)
                # Re-express the band at the chained level.
                #
                # `level` is an INDEX — anchored weeks ago and moved only by
                # matched price relatives — while raw_lo/raw_hi are absolute
                # prices from this date's basket. Storing the two side by side
                # meant they answered different questions, and they drifted
                # apart: on the live catalogue 94 of 727 points (12.9%, across
                # 23 series) had center_price falling OUTSIDE its own
                # [min_price, max_price]. That is not a display glitch, it is
                # two incompatible quantities in adjacent columns — and it
                # rendered as a chart whose line ran along the frame edge, or
                # vanished entirely, above or below its band.
                #
                # Scaling by the basket's own median makes the band a relative
                # dispersion carried to wherever the index sits, so the center
                # is inside it by construction. On an anchor date `level` IS
                # that median, so this is the identity there.
                raw_center = _median(matched_prices)
                scale = (level / raw_center) if raw_center else 1.0
                band_lo = round(raw_lo * scale)
                band_hi = round(raw_hi * scale)
                records.append((
                    category, group_type, group_key, date,
                    len(prices_now), used, round(level), method,
                    band_lo, band_hi, basket_size,
                ))

        with self._conn:  # transaction
            self._conn.execute("DELETE FROM price_trends")
            self._conn.executemany(
                """
                INSERT INTO price_trends
                    (category, group_type, group_key, scrape_date,
                     sample_count, used_count, center_price, method,
                     min_price, max_price, basket_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
        return len(records)

    @staticmethod
    def _trend_group_type(category: str) -> str:
        """The group_type a category's trends are stored under."""
        return "spec" if category == "ram" else "model"

    def get_price_trends(
        self, category: str, group_key: Optional[str] = None,
        group_type: Optional[str] = None,
        max_dates: Optional[int] = _TREND_MAX_DATES,
    ) -> list[dict]:
        """
        Trend series for a category. With group_key -> one group's time series;
        without -> all groups in the category. Ordered by group then date.
        group_type defaults to the category's axis (ram='spec', else 'model').

        `max_dates` keeps only the N most recent scrape dates in the category.
        The cut is per-category, not per-group, so every group on the page spans
        the same x-axis. Pass None for the full history.
        """
        if group_type is None:
            group_type = self._trend_group_type(category)
        sql = """
            SELECT group_key, scrape_date, center_price, method,
                   min_price, max_price, sample_count, used_count
            FROM price_trends
            WHERE category = ? AND group_type = ?
        """
        params: list = [category, group_type]
        if group_key is not None:
            sql += " AND group_key = ?"
            params.append(group_key)
        if max_dates is not None:
            sql += """
              AND scrape_date IN (
                    SELECT DISTINCT scrape_date FROM price_trends
                    WHERE category = ? AND group_type = ?
                    ORDER BY scrape_date DESC LIMIT ?
              )
            """
            params += [category, group_type, max_dates]
        sql += " ORDER BY group_key, scrape_date"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def list_trend_groups(
        self, category: str, group_type: Optional[str] = None,
    ) -> list[dict]:
        """
        Distinct trend groups in a category with their latest center_price,
        latest min/max band and most-recent sample_count, plus a representative
        thumbnail (one in-stock listing's image per group), for the trends page.
        Latest = max date. group_type defaults to the category's axis
        (ram='spec', else 'model').
        """
        if group_type is None:
            group_type = self._trend_group_type(category)
        rows = self._conn.execute(
            """
            SELECT t.group_key,
                   t.center_price AS latest_price,
                   t.min_price,
                   t.max_price,
                   t.sample_count
            FROM price_trends t
            JOIN (
                SELECT group_key, MAX(scrape_date) AS d
                FROM price_trends
                WHERE category = ? AND group_type = ?
                GROUP BY group_key
            ) last ON last.group_key = t.group_key AND last.d = t.scrape_date
            WHERE t.category = ? AND t.group_type = ?
            ORDER BY t.group_key
            """,
            (category, group_type, category, group_type),
        ).fetchall()
        groups = [dict(r) for r in rows]
        if group_type == "model":
            self._attach_model_thumbnails(category, groups)
        return groups

    def _attach_model_thumbnails(self, category: str, groups: list[dict]) -> None:
        """
        Add a `thumbnail_url` to each model group: pick one current listing's
        non-null thumbnail whose extracted specs.model matches the group_key.
        Cheapest matching listing wins (most representative of the segment).
        Scoped to active listings only, via parts.latest_price rather than a
        full price_log scan — a delisted row's thumbnail must not win.
        """
        rows = self._conn.execute(
            """
            SELECT json_extract(p.specs, '$.model') AS model,
                   p.thumbnail_url                  AS thumbnail_url,
                   MIN(p.latest_price)              AS _min
            FROM parts p
            WHERE p.category = ?
              AND p.is_active = 1
              AND p.latest_price IS NOT NULL
              AND p.thumbnail_url IS NOT NULL
              AND json_extract(p.specs, '$.model') IS NOT NULL
            GROUP BY json_extract(p.specs, '$.model')
            """,
            (category,),
        ).fetchall()
        thumbs = {r["model"]: r["thumbnail_url"] for r in rows}
        for g in groups:
            g["thumbnail_url"] = thumbs.get(g["group_key"])

    def get_shared_build(self, code: str) -> Optional[dict]:
        """
        Retrieve a shared build by its code.

        Args:
            code: 6-char alphanumeric code

        Returns:
            dict of {slot: part_id} or None if not found
        """
        row = self._conn.execute(
            "SELECT build_json FROM shared_builds WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["build_json"])

    def resolve_shared_build(self, code: str) -> Optional[dict]:
        """
        Resolve a shared build code to a dict of {slot: full_part_dict}.

        Handles both the current per-slot shape ({"id": <int>, "qty": <int>,
        "price_at_share": <int|None>}) and the legacy bare-int-per-slot shape
        ({"slot": <part_id>}) written by codes created before qty/price
        snapshot support -- a bare int is treated as qty=1 with no snapshot.

        A part that's since been delisted, or lost its price, is still
        returned (not skipped) -- a build silently missing a component is
        more confusing than one that shows the component as unavailable.
        Callers get is_active/delisted_at/price_at_share alongside the
        current price_pkr (which may be NULL) to decide how to render it.

        Args:
            code: 6-char alphanumeric code

        Returns:
            dict of {slot: part_dict} or None if code not found.
            part_dict includes: id, source, name, category, url, thumbnail_url,
            specs, price_pkr, is_active, delisted_at, price_at_share, qty
        """
        slot_data = self.get_shared_build(code)
        if slot_data is None:
            return None

        # Normalize both shapes into (part_id, qty, price_at_share) per slot.
        id_to_slot: dict[int, str] = {}
        slot_meta: dict[str, dict] = {}
        for slot, val in slot_data.items():
            if val is None:
                continue
            if isinstance(val, dict):
                part_id = val.get("id")
                qty = val.get("qty", 1)
                price_at_share = val.get("price_at_share")
            else:
                # Legacy shape: bare part_id.
                part_id = val
                qty = 1
                price_at_share = None
            if part_id is None:
                continue
            id_to_slot[part_id] = slot
            slot_meta[slot] = {"qty": qty, "price_at_share": price_at_share}

        if not id_to_slot:
            return {}

        placeholders = ",".join("?" * len(id_to_slot))
        rows = self._conn.execute(
            f"""
            SELECT p.id, p.source, p.name, p.category, p.url, p.thumbnail_url, p.specs,
                   p.latest_price AS price_pkr, p.is_active, p.delisted_at
            FROM parts p
            WHERE p.id IN ({placeholders})
            """,
            list(id_to_slot.keys()),
        ).fetchall()

        result = {}
        for row in rows:
            d = dict(row)
            slot = id_to_slot[d["id"]]
            meta = slot_meta[slot]
            d["is_active"] = bool(d["is_active"])
            d["qty"] = meta["qty"]
            d["price_at_share"] = meta["price_at_share"]
            result[slot] = d
        return result

    def resolve_part_status(self, part_ids: list[int]) -> dict[int, dict]:
        """
        Answer "is this part still listed?" for a set of parts in one query.

        Shared builds use this to flag a part that has gone away since the link
        was created. A favourites/watch-list feature needs the same answer, so
        this is deliberately generic — it takes ids and returns facts, with no
        knowledge of what is asking.

        Unknown ids are simply absent from the result.
        """
        if not part_ids:
            return {}
        placeholders = ",".join("?" * len(part_ids))
        rows = self._conn.execute(
            f"""
            SELECT id, is_active, last_seen_at, delisted_at, latest_price
            FROM parts WHERE id IN ({placeholders})
            """,
            list(part_ids),
        ).fetchall()
        return {
            r["id"]: {
                "is_active": bool(r["is_active"]),
                "last_seen_at": r["last_seen_at"],
                "delisted_at": r["delisted_at"],
                "latest_price": r["latest_price"],
            }
            for r in rows
        }

    # ------------------------------------------------------------------
    # Prebuilts
    # ------------------------------------------------------------------

    def upsert_prebuilts(self, prebuilts: list[dict]) -> int:
        """
        Upsert a list of scraped prebuilt PCs.
        Each dict must have: name, url, source, scraped_at, price_pkr (int|None),
        thumbnail_url (str|None), components (dict|None).
        Returns number of rows upserted.
        """
        upserted = 0
        seen_ids: dict[str, set[int]] = {}
        cur = self._conn.cursor()
        for p in prebuilts:
            try:
                source_id = _slug(p["url"])
            except ValueError as exc:
                # Same rule as upsert_products: one malformed URL in a batch
                # of ~100 prebuilts must not abort the whole source's write
                # (and, since run_prebuilts.py calls this uncaught, every
                # source scraped after it in the same run).
                print(f"  SKIP prebuilt {p.get('name', '<unknown>')!r}: {exc}")
                continue
            components_json = json.dumps(p["components"], ensure_ascii=False) if p.get("components") else None
            row = cur.execute(
                """
                INSERT INTO prebuilts (source, source_id, name, url, thumbnail_url, price_pkr, components,
                                       name_norm, scraped_at, is_active, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    name          = excluded.name,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, prebuilts.thumbnail_url),
                    price_pkr     = excluded.price_pkr,
                    components    = excluded.components,
                    name_norm     = excluded.name_norm,
                    scraped_at    = excluded.scraped_at,
                    is_active     = 1,
                    delisted_at   = NULL,
                    last_seen_at  = excluded.last_seen_at,
                    updated_at    = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                RETURNING id
                """,
                (
                    p["source"], source_id, p["name"], p["url"],
                    p.get("thumbnail_url"), p.get("price_pkr"),
                    components_json, normalize_name(p["name"]), p["scraped_at"], p["scraped_at"],
                ),
            ).fetchone()
            seen_ids.setdefault(p["source"], set()).add(row["id"])
            upserted += 1
        self._conn.commit()
        self._last_seen_prebuilt_ids = seen_ids
        return upserted

    def deactivate_unseen_prebuilts(self, source: str) -> int:
        """
        Prebuilt equivalent of deactivate_unseen_parts(). Same gating rule:
        only call after a scrape that actually returned products. Same
        no-scratch-table treatment for the same reason — see the docstring
        on deactivate_unseen_parts().
        """
        seen = getattr(self, "_last_seen_prebuilt_ids", {}).get(source, set())
        if not seen:
            return 0
        with self._conn:
            active_rows = self._conn.execute(
                "SELECT id FROM prebuilts WHERE source = ? AND is_active = 1",
                (source,),
            ).fetchall()
            to_deactivate = [r["id"] for r in active_rows if r["id"] not in seen]
            if not to_deactivate:
                return 0
            total = 0
            for i in range(0, len(to_deactivate), 900):
                chunk = to_deactivate[i : i + 900]
                placeholders = ",".join("?" * len(chunk))
                cur = self._conn.execute(
                    f"""
                    UPDATE prebuilts SET is_active = 0,
                                         delisted_at = COALESCE(delisted_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                                         updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE is_active = 1
                      AND id IN ({placeholders})
                    """,
                    chunk,
                )
                total += cur.rowcount
        return total

    def list_prebuilts(
        self,
        *,
        source: Optional[str] = None,
        min_price: Optional[int] = None,
        max_price: Optional[int] = None,
        q: Optional[str] = None,
        cpu_brand: Optional[str] = None,
        gpu_brand: Optional[str] = None,
        sort: str = "price_asc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return (items, total) for the prebuilts listing page."""
        conditions: list[str] = ["price_pkr IS NOT NULL", "is_active = 1"]
        params: list = []

        if source:
            conditions.append("source = ?")
            params.append(source)
        if min_price is not None:
            conditions.append("price_pkr >= ?")
            params.append(min_price)
        if max_price is not None:
            conditions.append("price_pkr <= ?")
            params.append(max_price)
        if q:
            for token in search_tokens(q):
                conditions.append("name_norm LIKE ? ESCAPE '\\'")
                params.append(f"% {_like_escape(token)}%")
        if cpu_brand:
            brand = cpu_brand.lower()
            if brand == "amd":
                conditions.append("(LOWER(json_extract(components, '$.cpu')) LIKE '%ryzen%' OR LOWER(json_extract(components, '$.cpu')) LIKE '%amd%')")
            elif brand == "intel":
                conditions.append("(LOWER(json_extract(components, '$.cpu')) LIKE '%intel%' OR LOWER(json_extract(components, '$.cpu')) LIKE '%core i%')")
        if gpu_brand:
            brand = gpu_brand.lower()
            if brand == "nvidia":
                conditions.append("(LOWER(json_extract(components, '$.gpu')) LIKE '%rtx%' OR LOWER(json_extract(components, '$.gpu')) LIKE '%gtx%' OR LOWER(json_extract(components, '$.gpu')) LIKE '%nvidia%')")
            elif brand == "amd":
                conditions.append("(LOWER(json_extract(components, '$.gpu')) LIKE '%amd%' OR LOWER(json_extract(components, '$.gpu')) LIKE '%radeon%')")
            elif brand == "intel":
                conditions.append("LOWER(json_extract(components, '$.gpu')) LIKE '%arc%'")

        where = "WHERE " + " AND ".join(conditions)
        order = "ORDER BY price_pkr ASC, id ASC" if sort == "price_asc" else "ORDER BY price_pkr DESC, id ASC"

        total = self._conn.execute(
            f"SELECT COUNT(*) FROM prebuilts {where}", params
        ).fetchone()[0]

        rows = self._conn.execute(
            f"""
            SELECT id, source, source_id, name, url, thumbnail_url, price_pkr, components, scraped_at
            FROM prebuilts {where} {order}
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

        return [dict(r) for r in rows], total

    def get_prebuilt(self, prebuilt_id: int) -> Optional[dict]:
        """Return a single prebuilt by id."""
        row = self._conn.execute(
            "SELECT * FROM prebuilts WHERE id = ?", (prebuilt_id,)
        ).fetchone()
        return dict(row) if row else None

    def prebuilt_stats(self) -> dict:
        total = self._conn.execute(
            "SELECT COUNT(*) FROM prebuilts WHERE is_active = 1"
        ).fetchone()[0]
        by_source = self._conn.execute(
            "SELECT source, COUNT(*) as n FROM prebuilts WHERE is_active = 1 GROUP BY source"
        ).fetchall()
        return {"total": total, "by_source": {r["source"]: r["n"] for r in by_source}}

    def latest_scrape_date(self) -> str | None:
        """
        Most recent scrape date (YYYY-MM-DD) present in price_log — i.e. the
        current trend bucket. The heal rerun pins its rows to this via
        SCRAPE_AS_OF_DATE so a late-merged fix joins the weekly bucket instead of
        splitting trends onto a new day. None if price_log is empty.
        """
        row = self._conn.execute(
            "SELECT substr(MAX(scraped_at), 1, 10) FROM price_log"
        ).fetchone()
        return row[0] if row and row[0] else None

    def counts_by_source_category(self) -> dict[tuple[str, str], int]:
        """
        Active-row count keyed by (source, category). Feeds the health check's
        per-category anomaly detection (a category that had rows but scraped 0).
        """
        rows = self._conn.execute(
            "SELECT source, category, COUNT(*) AS n FROM parts "
            "WHERE is_active = 1 GROUP BY source, category"
        ).fetchall()
        return {(r["source"], r["category"]): r["n"] for r in rows}

    def stats(self) -> dict:
        """
        Quick summary — useful for CLI output, and the landing page's per-source
        cards + STALE ribbon via GET /api/stats.

        Used to be six round trips (parts total, by-source, by-category,
        price_log total, then source_health's own two). The active-parts
        counts and the price_log total are pulled together with UNION ALL —
        a single grouped result the by_source/by_category dicts are pivoted
        from in Python — and source_health is down to one query itself, so
        this is two round trips total.
        """
        rows = self._conn.execute(
            """
            SELECT 'part' AS kind, source, category, COUNT(*) AS n
            FROM parts WHERE is_active = 1
            GROUP BY source, category
            UNION ALL
            SELECT 'price_log', NULL, NULL, COUNT(*) FROM price_log
            """
        ).fetchall()

        parts_total = 0
        price_rows = 0
        by_source: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for r in rows:
            if r["kind"] == "part":
                by_source[r["source"]] = by_source.get(r["source"], 0) + r["n"]
                by_category[r["category"]] = by_category.get(r["category"], 0) + r["n"]
                parts_total += r["n"]
            else:
                price_rows = r["n"]

        return {
            "total_parts": parts_total,
            "total_price_rows": price_rows,
            "by_source": by_source,
            "by_category": dict(sorted(by_category.items())),
            "sources": self.source_health("parts"),
        }

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def get_db(
    path: str | Path = _DEFAULT_DB,
    *,
    allow_remote_migrations: bool | None = None,
) -> Database:
    """
    Open (or create) the PPC database.

    When TURSO_DATABASE_URL is set the connection is remote (Turso/libSQL) and
    `path` is ignored; otherwise it is the local SQLite file at `path`.

    allow_remote_migrations: True to migrate a remote target, False to connect
    without touching its schema, None (default) to refuse a remote target
    outright unless ALLOW_REMOTE_MIGRATIONS=1. See _remote_migration_mode().
    """
    return Database(path, allow_remote_migrations=allow_remote_migrations)


def backup_db(path: str | Path = _DEFAULT_DB, keep: int = 10) -> Optional[Path]:
    """
    Snapshot the DB to <name>.bak.<UTC timestamp> before a scrape mutates it.
    Uses SQLite's online backup API so it is safe on a WAL database.
    Keeps only the newest `keep` snapshots. Returns the backup path (None if
    the source DB does not exist yet).

    Remote (Turso) mode: the local online-backup API does not apply, so this is
    a no-op returning None. Turso snapshots are taken with `turso db dump`
    (or Turso PITR on paid tiers) — see docs/DB_migration.md.
    """
    if os.getenv("TURSO_DATABASE_URL"):
        return None
    src = Path(path)
    if not src.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = src.with_name(f"{src.name}.bak.{stamp}")
    con = sqlite3.connect(str(src))
    try:
        bck = sqlite3.connect(str(dest))
        try:
            con.backup(bck)
        finally:
            bck.close()
    finally:
        con.close()

    # Only prune snapshots this function created: <name>.bak.YYYYMMDD_HHMMSS.
    # A looser glob previously matched hand-named snapshots (pre-migration
    # backups) and rotated them away, while missing the undated <name>.bak.
    snaps = sorted(
        p for p in src.parent.glob(f"{src.name}.bak.*")
        if re.fullmatch(r"\d{8}_\d{6}", p.name[len(src.name) + 5:])
    )
    for old in snaps[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass
    return dest
