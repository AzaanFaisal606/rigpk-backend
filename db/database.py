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

import math
import os
import re
import sqlite3
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json
from scrapers.spec_extractor import extract_specs

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


def _slug(url: str) -> str:
    """Derive a stable, short identifier from a product URL."""
    url = re.sub(r"https?://[^/]+/", "", url).rstrip("/")
    url = re.sub(r"[^\w-]", "-", url)
    url = re.sub(r"-{2,}", "-", url)
    return url[:200]


def _median(values: list[int]) -> float:
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
}

# How many of the most recent scrape dates a trend series shows. Scrape dates
# are irregular, so this is "the last N scrapes", not a time window. At ~300px
# of sparkline the points and their hover targets get unusable past a handful.
# Temporary ceiling until the trends page grows a proper range filter.
_TREND_MAX_DATES = 5


def _is_blocked(name: str, category: str) -> bool:
    lower = name.lower()
    for term in _GLOBAL_BLOCKLIST:
        if term in lower:
            return True
    for term in _CATEGORY_BLOCKLIST.get(category, ()):
        if term in lower:
            return True
    return False


_VALID_SPEC_KEYS = frozenset({
    "brand", "socket", "vram", "ddr_type", "speed", "chipset",
    "wattage", "rating", "form_factor", "type", "aio_size",
    "fan_size", "interface", "capacity", "model",
})

_CATEGORY_SPEC_KEYS: dict[str, list[str]] = {
    "cpu":         ["brand", "socket", "model"],
    "gpu":         ["brand", "vram", "model"],
    "ram":         ["brand", "ddr_type", "speed", "capacity"],
    "motherboard": ["brand", "socket", "chipset"],
    "psu":         ["brand", "wattage", "rating"],
    "case":        ["brand", "form_factor"],
    "cooling":     ["brand", "type", "aio_size", "fan_size"],
    "ssd":         ["brand", "interface", "capacity"],
    "hdd":         ["brand"],
    "monitor":     ["brand"],
}


class Database:
    def __init__(self, path: str | Path | None = None):
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
        self._apply_schema()

    def _apply_schema(self):
        # Remote DB: apply schema + migrations once per process (see module note).
        if self._remote and self._target in _REMOTE_SCHEMA_APPLIED:
            return
        with open(_SCHEMA, encoding="utf-8") as f:
            self._conn.executescript(f.read())
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

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert_products(self, products: list[dict]) -> int:
        """
        Upsert a list of scraped products and log their prices.
        Returns the number of price_log rows inserted.
        """
        inserted = 0
        skipped = 0
        seen_ids: dict[str, set[int]] = {}
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
                continue
            if _is_blocked(p["name"], p["category"]):
                skipped += 1
                continue
            source_id = _slug(p["url"])
            thumbnail = p.get("thumbnail_url")

            raw_specs = extract_specs(p["name"], p["category"])
            specs_json = json.dumps(raw_specs, ensure_ascii=False) if raw_specs else None

            row = cur.execute(
                """
                INSERT INTO parts (source, source_id, name, category, url, thumbnail_url, specs,
                                   is_active, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    name          = excluded.name,
                    category      = excluded.category,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, parts.thumbnail_url),
                    specs         = excluded.specs,
                    is_active     = 1,
                    last_seen_at  = excluded.last_seen_at,
                    updated_at    = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                RETURNING id
                """,
                (p["source"], source_id, p["name"], p["category"], p["url"], thumbnail, specs_json,
                 p["scraped_at"]),
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
            except sqlite3.IntegrityError:
                pass

        self._conn.commit()
        self._last_seen_ids = seen_ids
        return inserted

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
        """
        seen = getattr(self, "_last_seen_ids", {}).get(source, set())
        if not seen:
            return 0
        placeholders = ",".join("?" * len(seen))
        with self._conn:
            cur = self._conn.execute(
                f"""
                UPDATE parts SET is_active = 0,
                                 updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE source = ? AND is_active = 1 AND id NOT IN ({placeholders})
                """,
                [source, *seen],
            )
        return cur.rowcount

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
        latest = self._conn.execute(
            """
            SELECT source, ok, products, finished_at, error,
                   before_active, after_active, swept
            FROM scrape_runs r
            WHERE kind = ?
              AND id = (SELECT MAX(id) FROM scrape_runs s
                        WHERE s.source = r.source AND s.kind = r.kind)
            """,
            (kind,),
        ).fetchall()
        successes = self._conn.execute(
            """
            SELECT source, MAX(finished_at) AS at
            FROM scrape_runs WHERE kind = ? AND ok = 1 GROUP BY source
            """,
            (kind,),
        ).fetchall()
        last_ok = {r["source"]: r["at"] for r in successes}

        return {
            r["source"]: {
                "stale": not r["ok"],
                "last_run_at": r["finished_at"],
                "last_success_at": last_ok.get(r["source"]),
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
            build: dict of {slot: part_id} (e.g. {"cpu": 42, "gpu": 17})

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
    ) -> tuple[list[dict], int]:
        """
        Return (items, total) for the market listing page.
        Items have the latest price per part. NULL-price rows excluded.
        specs_filter: e.g. {"brand": "AMD", "socket": "AM5"}
        """
        conditions: list[str] = ["pl.price_pkr IS NOT NULL", "p.is_active = 1"]
        params: list = []

        if category:
            conditions.append("p.category = ?")
            params.append(category)
        if source:
            conditions.append("p.source = ?")
            params.append(source)
        if min_price is not None:
            conditions.append("pl.price_pkr >= ?")
            params.append(min_price)
        if max_price is not None:
            conditions.append("pl.price_pkr <= ?")
            params.append(max_price)
        if q:
            conditions.append("p.name LIKE ?")
            params.append(f"%{q}%")
        if specs_filter:
            for key, value in specs_filter.items():
                if key in _VALID_SPEC_KEYS:
                    conditions.append("json_extract(p.specs, ?) = ?")
                    params.extend([f"$.{key}", value])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        order = (
            "ORDER BY pl.price_pkr ASC"
            if sort == "price_asc"
            else "ORDER BY pl.price_pkr DESC"
        )

        base_query = f"""
            FROM parts p
            JOIN price_log pl ON pl.id = (
                SELECT id FROM price_log
                WHERE part_id = p.id
                ORDER BY scraped_at DESC
                LIMIT 1
            )
            {where}
        """

        total = self._conn.execute(
            f"SELECT COUNT(*) {base_query}", params
        ).fetchone()[0]

        rows = self._conn.execute(
            f"""
            SELECT p.id, p.source, p.name, p.category, p.url, p.thumbnail_url,
                   p.specs, pl.price_pkr
            {base_query}
            {order}
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
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
                  AND json_extract(specs, ?) IS NOT NULL
                ORDER BY val
                """,
                (json_path, category, json_path),
            ).fetchall()
            values = [r[0] for r in rows if r[0]]
            if values:
                result[key] = values
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

    # Minimum listings in a bucket to use a trimmed mean; below this we fall
    # back to a plain median (too few points to trim meaningfully).
    _TREND_MIN_TRIM = 5
    _TREND_TRIM_FRAC = 0.10
    # Band (min/max) trim: drop the most extreme 5% each end so mispriced
    # outlier listings don't blow out the displayed range. Center uses the
    # 10% trim above; the band is wider (5%) to still show a real spread.
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
                   d.price_pkr AS price
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

        # Bucket: (category, group_type, group_key, date) -> [prices]
        buckets: dict[tuple[str, str, str, str], list[int]] = {}
        for r in rows:
            try:
                specs = json.loads(r["specs"]) if r["specs"] else None
            except (json.JSONDecodeError, TypeError):
                specs = None
            grp = self._trend_group(r["category"], specs)
            if grp is None:
                continue
            group_type, group_key = grp
            key = (r["category"], group_type, group_key, r["scrape_date"])
            buckets.setdefault(key, []).append(int(r["price"]))

        records = []
        for (category, group_type, group_key, date), prices in buckets.items():
            n = len(prices)
            if n >= self._TREND_MIN_TRIM:
                center, used = _trimmed_mean(prices, self._TREND_TRIM_FRAC)
                method = "trimmed_mean"
            else:
                # median uses 1 value (odd n) or the middle 2 (even n)
                center, used = _median(prices), (1 if n % 2 else 2)
                method = "median"
            band_lo, band_hi = _trimmed_band(prices, self._TREND_BAND_FRAC)
            records.append((
                category, group_type, group_key, date,
                n, used, round(center), method, band_lo, band_hi,
            ))

        with self._conn:  # transaction
            self._conn.execute("DELETE FROM price_trends")
            self._conn.executemany(
                """
                INSERT INTO price_trends
                    (category, group_type, group_key, scrape_date,
                     sample_count, used_count, center_price, method, min_price, max_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        """
        rows = self._conn.execute(
            """
            SELECT json_extract(p.specs, '$.model') AS model,
                   p.thumbnail_url AS thumbnail_url,
                   MIN(pl.price_pkr)               AS _min
            FROM parts p
            JOIN price_log pl ON pl.part_id = p.id
            WHERE p.category = ?
              AND pl.price_pkr IS NOT NULL
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
        Skips slots where the part no longer exists in DB.

        Args:
            code: 6-char alphanumeric code

        Returns:
            dict of {slot: part_dict} or None if code not found.
            part_dict includes: id, source, name, category, url, thumbnail_url, specs, price_pkr
        """
        slot_ids = self.get_shared_build(code)
        if slot_ids is None:
            return None

        # Collect all valid part_ids
        id_to_slot: dict[int, str] = {part_id: slot for slot, part_id in slot_ids.items() if part_id is not None}
        if not id_to_slot:
            return {}

        placeholders = ",".join("?" * len(id_to_slot))
        rows = self._conn.execute(
            f"""
            SELECT p.id, p.source, p.name, p.category, p.url, p.thumbnail_url, p.specs, pl.price_pkr
            FROM parts p
            JOIN price_log pl ON pl.id = (
                SELECT id FROM price_log WHERE part_id = p.id ORDER BY scraped_at DESC LIMIT 1
            )
            WHERE p.id IN ({placeholders})
            """,
            list(id_to_slot.keys()),
        ).fetchall()

        result = {}
        for row in rows:
            d = dict(row)
            slot = id_to_slot[d["id"]]
            result[slot] = d
        return result

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
            source_id = _slug(p["url"])
            components_json = json.dumps(p["components"], ensure_ascii=False) if p.get("components") else None
            row = cur.execute(
                """
                INSERT INTO prebuilts (source, source_id, name, url, thumbnail_url, price_pkr, components, scraped_at,
                                       is_active, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    name          = excluded.name,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, prebuilts.thumbnail_url),
                    price_pkr     = excluded.price_pkr,
                    components    = excluded.components,
                    scraped_at    = excluded.scraped_at,
                    is_active     = 1,
                    last_seen_at  = excluded.last_seen_at,
                    updated_at    = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                RETURNING id
                """,
                (
                    p["source"], source_id, p["name"], p["url"],
                    p.get("thumbnail_url"), p.get("price_pkr"),
                    components_json, p["scraped_at"], p["scraped_at"],
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
        only call after a scrape that actually returned products.
        """
        seen = getattr(self, "_last_seen_prebuilt_ids", {}).get(source, set())
        if not seen:
            return 0
        placeholders = ",".join("?" * len(seen))
        with self._conn:
            cur = self._conn.execute(
                f"""
                UPDATE prebuilts SET is_active = 0,
                                     updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE source = ? AND is_active = 1 AND id NOT IN ({placeholders})
                """,
                [source, *seen],
            )
        return cur.rowcount

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
            conditions.append("name LIKE ?")
            params.append(f"%{q}%")
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
        order = "ORDER BY price_pkr ASC" if sort == "price_asc" else "ORDER BY price_pkr DESC"

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
        """Quick summary — useful for CLI output."""
        parts_total = self._conn.execute(
            "SELECT COUNT(*) FROM parts WHERE is_active = 1"
        ).fetchone()[0]
        by_source = self._conn.execute(
            "SELECT source, COUNT(*) as n FROM parts WHERE is_active = 1 GROUP BY source"
        ).fetchall()
        by_cat = self._conn.execute(
            "SELECT category, COUNT(*) as n FROM parts WHERE is_active = 1 GROUP BY category ORDER BY category"
        ).fetchall()
        price_rows = self._conn.execute("SELECT COUNT(*) FROM price_log").fetchone()[0]
        return {
            "total_parts": parts_total,
            "total_price_rows": price_rows,
            "by_source": {r["source"]: r["n"] for r in by_source},
            "by_category": {r["category"]: r["n"] for r in by_cat},
            "sources": self.source_health("parts"),
        }

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def get_db(path: str | Path = _DEFAULT_DB) -> Database:
    """
    Open (or create) the PPC database.

    When TURSO_DATABASE_URL is set the connection is remote (Turso/libSQL) and
    `path` is ignored; otherwise it is the local SQLite file at `path`.
    """
    return Database(path)


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

    snaps = sorted(src.parent.glob(f"{src.name}.bak.*"))
    for old in snaps[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass
    return dest
