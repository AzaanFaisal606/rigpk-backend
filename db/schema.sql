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
    is_active     INTEGER NOT NULL DEFAULT 1, -- 0 = not seen in last successful scrape of its source
    last_seen_at  TEXT    DEFAULT NULL,       -- ISO 8601 UTC of the last scrape that saw this part
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

CREATE TABLE IF NOT EXISTS shared_builds (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  code       TEXT NOT NULL UNIQUE,
  build_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_shared_builds_code ON shared_builds(code);

-- Prebuilt PCs scraped from retailers (separate from individual parts)
-- components is a JSON object: {"cpu": "...", "gpu": "...", "ram": "...", ...}
CREATE TABLE IF NOT EXISTS prebuilts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT    NOT NULL,          -- e.g. "zestrogaming.com"
    source_id     TEXT    NOT NULL,          -- stable slug from URL
    name          TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    thumbnail_url TEXT,
    price_pkr     INTEGER,                   -- NULL = price hidden / out of stock
    components    TEXT    DEFAULT NULL,      -- JSON: {"cpu":..., "gpu":..., "ram":..., ...}
    scraped_at    TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1, -- 0 = not seen in last successful scrape of its source
    last_seen_at  TEXT    DEFAULT NULL,       -- ISO 8601 UTC of the last scrape that saw this prebuilt
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_prebuilts_source    ON prebuilts(source);
CREATE INDEX IF NOT EXISTS idx_prebuilts_price     ON prebuilts(price_pkr);

-- Precomputed price trends — one row per (category, group, scrape_date).
-- A "group" is either a model (gpu/cpu, e.g. "RTX 4070") or a spec bucket
-- (e.g. ram "DDR4-3200"); group_type discriminates. Rebuilt after each scrape
-- from price_log via rebuild_price_trends(). Denormalized rollup — no FK to
-- parts, so a trend point survives even after its source listings go OOS.
CREATE TABLE IF NOT EXISTS price_trends (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    category      TEXT    NOT NULL,          -- gpu | cpu | ram | ...
    group_type    TEXT    NOT NULL,          -- 'model' | 'spec'
    group_key     TEXT    NOT NULL,          -- "RTX 4070" | "Ryzen 5 5600X" | "DDR4-3200"
    scrape_date   TEXT    NOT NULL,          -- YYYY-MM-DD (one snapshot bucket)
    sample_count  INTEGER NOT NULL,          -- listings before trim
    used_count    INTEGER NOT NULL,          -- listings the center value used
    center_price  INTEGER NOT NULL,          -- trend-line value (trimmed mean or median)
    method        TEXT    NOT NULL,          -- 'trimmed_mean' (n>=5) | 'median' (n<5)
    min_price     INTEGER NOT NULL,          -- band low  (5%-trimmed range; n<5 full)
    max_price     INTEGER NOT NULL,          -- band high (5%-trimmed range; n<5 full)
    computed_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (category, group_type, group_key, scrape_date)
);

CREATE INDEX IF NOT EXISTS idx_trends_lookup
    ON price_trends(category, group_type, group_key, scrape_date);

-- One row per scraper run per source. Written by the orchestrators whether the
-- run succeeded or not, so "this retailer's data is stale" is a recorded fact
-- rather than something inferred from row counts. The landing page reads the
-- latest row per source to decide whether to show the STALE ribbon.
--
-- ok = 1 means the run is trusted and the freshness sweep ran. ok = 0 means the
-- source could not be scraped (0 products, exception, or circuit breaker) — the
-- sweep was skipped and its existing rows were left active but are now stale.
CREATE TABLE IF NOT EXISTS scrape_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,          -- e.g. "amdhouse.pk"
    kind        TEXT    NOT NULL DEFAULT 'parts',   -- 'parts' | 'prebuilt'
    started_at  TEXT    NOT NULL,          -- ISO 8601 UTC
    finished_at TEXT    NOT NULL,          -- ISO 8601 UTC
    products    INTEGER NOT NULL DEFAULT 0,-- items the scraper returned
    ok          INTEGER NOT NULL DEFAULT 0,-- 1 = trusted run, sweep applied
    swept       INTEGER NOT NULL DEFAULT 0,-- rows marked inactive by the sweep
    before_active INTEGER,                   -- active rows for this source before the run
    after_active  INTEGER,                   -- active rows for this source after the run
    error       TEXT                        -- failure summary when ok = 0
);

CREATE INDEX IF NOT EXISTS idx_scrape_runs_source
    ON scrape_runs(source, kind, finished_at);
