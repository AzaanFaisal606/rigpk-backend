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
    name_norm     TEXT    DEFAULT NULL,      -- lowercased, tokenised, space-padded name for search
    latest_price  INTEGER DEFAULT NULL,      -- newest price_log price; cache, price_log is truth
    is_active     INTEGER NOT NULL DEFAULT 1, -- 0 = not seen in last successful scrape of its source
    last_seen_at  TEXT    DEFAULT NULL,       -- ISO 8601 UTC of the last scrape that saw this part
    delisted_at   TEXT    DEFAULT NULL,      -- ISO 8601 UTC when the sweep marked it inactive
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
-- Search matches `name_norm LIKE '% token%'`, which cannot use an index for the
-- leading wildcard, but category-scoped searches still narrow the scan first.
CREATE INDEX IF NOT EXISTS idx_parts_category_active ON parts(category, is_active);
-- idx_parts_cat_active_price (category, is_active, latest_price) — covers the
-- market page's list_parts() query, filter + ORDER BY in one index — is
-- created in _migrate() instead of here, on purpose: it's not safe in this
-- executescript(), which runs before _migrate()'s ALTERs add latest_price
-- and is_active to a database created before this file's CREATE TABLE
-- carried those columns.
CREATE INDEX IF NOT EXISTS idx_price_log_part ON price_log(part_id);
CREATE INDEX IF NOT EXISTS idx_price_log_time ON price_log(scraped_at);
-- Serves the "latest price per part" correlated subquery in list_parts():
-- SELECT id FROM price_log WHERE part_id = ? ORDER BY scraped_at DESC LIMIT 1.
-- Covering, so the subquery resolves from the index alone.
CREATE INDEX IF NOT EXISTS idx_price_log_latest ON price_log(part_id, scraped_at DESC, id);

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
    name_norm     TEXT    DEFAULT NULL,      -- lowercased, tokenised, space-padded name for search
    scraped_at    TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1, -- 0 = not seen in last successful scrape of its source
    last_seen_at  TEXT    DEFAULT NULL,       -- ISO 8601 UTC of the last scrape that saw this prebuilt
    delisted_at   TEXT    DEFAULT NULL,      -- ISO 8601 UTC when the sweep marked it inactive
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

-- Rows rejected by upsert_products' guards. Not user-facing — this exists so an
-- over-broad blocklist term is discoverable instead of silently eating real
-- products. `rule` names exactly which guard fired.
--
-- Deduped on (source, url) — the same rejected listing shows up again every
-- weekly scrape, so a repeat rejection UPDATEs this row (bumping
-- times_rejected, moving last_seen_at) instead of appending a fresh one
-- forever. url is what the rejected payload always carries at the point the
-- guards fire; source_id isn't derived until a row passes them. The unique
-- index that backs the dedup (and the ON CONFLICT below) is created in
-- _migrate() rather than here, since CREATE TABLE IF NOT EXISTS never runs
-- again on a DB that already has this table pre-dedup.
CREATE TABLE IF NOT EXISTS quarantined_rows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL,
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    price_pkr      INTEGER,
    url            TEXT,
    rule           TEXT NOT NULL,          -- "min_price:gpu:4000" | "blocklist:global:combo" | "blocklist:monitor:keyboard"
    times_rejected INTEGER NOT NULL DEFAULT 1,
    first_seen_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- idx_quarantine_time (on last_seen_at) and the (source, url) unique index
-- are created in _migrate() instead of here — an existing DB's table
-- predates the last_seen_at column, and this file's CREATE TABLE IF NOT
-- EXISTS never re-runs to add it, so an index on it here would fail on
-- every such DB before _migrate() gets a chance to add the column.
