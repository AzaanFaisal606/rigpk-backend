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

import os
import re
import sqlite3
import secrets
import string
from pathlib import Path
from typing import Optional
import json
from scrapers.spec_extractor import extract_specs

_SCHEMA = Path(__file__).parent / "schema.sql"
_DEFAULT_DB = Path(os.getenv("DB_PATH", str(Path(__file__).parent.parent / "data" / "ppc.db")))


def _slug(url: str) -> str:
    """Derive a stable, short identifier from a product URL."""
    url = re.sub(r"https?://[^/]+/", "", url).rstrip("/")
    url = re.sub(r"[^\w-]", "-", url)
    url = re.sub(r"-{2,}", "-", url)
    return url[:200]


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
    "fan_size", "interface", "capacity",
})

_CATEGORY_SPEC_KEYS: dict[str, list[str]] = {
    "cpu":         ["brand", "socket"],
    "gpu":         ["brand", "vram"],
    "ram":         ["brand", "ddr_type", "speed"],
    "motherboard": ["brand", "socket", "chipset"],
    "psu":         ["brand", "wattage", "rating"],
    "case":        ["brand", "form_factor"],
    "cooling":     ["brand", "type", "aio_size", "fan_size"],
    "ssd":         ["brand", "interface", "capacity"],
    "hdd":         ["brand"],
    "monitor":     ["brand"],
}


class Database:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()

    def _apply_schema(self):
        with open(_SCHEMA, encoding="utf-8") as f:
            self._conn.executescript(f.read())
        self._conn.commit()

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
        cur = self._conn.cursor()
        for p in products:
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

            cur.execute(
                """
                INSERT INTO parts (source, source_id, name, category, url, thumbnail_url, specs)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    name          = excluded.name,
                    category      = excluded.category,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, parts.thumbnail_url),
                    specs         = excluded.specs,
                    updated_at    = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (p["source"], source_id, p["name"], p["category"], p["url"], thumbnail, specs_json),
            )
            part_id = cur.execute(
                "SELECT id FROM parts WHERE source=? AND source_id=?",
                (p["source"], source_id),
            ).fetchone()["id"]

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
        return inserted

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

    def get_latest_prices(self, category: Optional[str] = None) -> list[dict]:
        """
        Return the most recent price for every product, optionally filtered
        by category.  Suitable for the web API's listing endpoint.
        """
        where = "WHERE p.category = ?" if category else ""
        params = (category,) if category else ()
        rows = self._conn.execute(
            f"""
            SELECT
                p.id, p.source, p.name, p.category, p.url, p.thumbnail_url,
                pl.price_pkr, pl.scraped_at
            FROM parts p
            JOIN price_log pl ON pl.id = (
                SELECT id FROM price_log
                WHERE part_id = p.id
                ORDER BY scraped_at DESC
                LIMIT 1
            )
            {where}
            ORDER BY p.category, p.name
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]

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
        conditions: list[str] = ["pl.price_pkr IS NOT NULL"]
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

    def stats(self) -> dict:
        """Quick summary — useful for CLI output."""
        parts_total = self._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
        by_source = self._conn.execute(
            "SELECT source, COUNT(*) as n FROM parts GROUP BY source"
        ).fetchall()
        by_cat = self._conn.execute(
            "SELECT category, COUNT(*) as n FROM parts GROUP BY category ORDER BY category"
        ).fetchall()
        price_rows = self._conn.execute("SELECT COUNT(*) FROM price_log").fetchone()[0]
        return {
            "total_parts": parts_total,
            "total_price_rows": price_rows,
            "by_source": {r["source"]: r["n"] for r in by_source},
            "by_category": {r["category"]: r["n"] for r in by_cat},
        }

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def get_db(path: str | Path = _DEFAULT_DB) -> Database:
    """Open (or create) the PPC database at the given path."""
    return Database(path)
