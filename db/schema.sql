-- Pakistan PC Picker — SQLite schema
--
-- Two-table design:
--   parts       — one row per unique product (identity, metadata)
--   price_log   — one row per scrape run per product (price history)
--
-- The `parts` table is keyed on (source, source_id) where source_id is a
-- stable slug derived from the product URL.  This lets the same physical
-- product accumulate price history across many scrape runs.
--
-- When migrating to PostgreSQL, swap INTEGER PRIMARY KEY AUTOINCREMENT for
-- SERIAL and remove the PRAGMA lines — everything else is standard SQL.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS parts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT    NOT NULL,          -- e.g. "czone.com.pk"
    source_id     TEXT    NOT NULL,          -- stable slug from URL
    name          TEXT    NOT NULL,
    category      TEXT    NOT NULL,          -- gpu | cpu | ram | ssd | hdd | psu | case | motherboard | cooling | monitor
    url           TEXT    NOT NULL,
    thumbnail_url TEXT,                      -- product image URL (may be NULL)
    specs         TEXT    DEFAULT NULL,      -- JSON dict e.g. {"brand":"AMD","socket":"AM5"}
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS price_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id    INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    price_pkr  INTEGER,                      -- NULL means out of stock / price hidden
    scraped_at TEXT NOT NULL,               -- ISO 8601 UTC
    UNIQUE (part_id, scraped_at)            -- prevent duplicate runs
);

-- Fast lookups used by the future web backend
CREATE INDEX IF NOT EXISTS idx_parts_category ON parts(category);
CREATE INDEX IF NOT EXISTS idx_parts_source   ON parts(source);
CREATE INDEX IF NOT EXISTS idx_price_log_part ON price_log(part_id);
CREATE INDEX IF NOT EXISTS idx_price_log_time ON price_log(scraped_at);
