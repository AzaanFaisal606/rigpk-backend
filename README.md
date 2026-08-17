# rigpk-backend

Backend for [RigPK](https://github.com/AzaanFaisal606/rigpk) — a PCPartPicker-style site for
Pakistan. Scrapes PC part prices from Pakistani retailers, stores them with full price history,
and serves a REST API consumed by the Next.js frontend.

---

## What It Does

- Scrapes 9 Pakistani retailers for GPU, CPU, RAM, SSD, HDD, PSU, case, motherboard, cooling and
  monitor parts
- Tracks ~10,200 parts (~7,900 currently active/in-stock) with full price history
- Tracks ~115 active prebuilt PCs from 3 retailers
- Filters out sold-out items at scrape time — server-side where the platform supports it,
  otherwise per-product during parsing
- Runs weekly via GitHub Actions: scrapes → writes the database → posts a Discord summary → opens
  an automated fix PR if a source or category breaks (see `docs/automation.md`)
- Exposes a REST API for filtering, in-browser search, build sharing/compatibility, price trends,
  and prebuilt browsing

## Tech Stack

| Layer | Tech |
|---|---|
| Language | Python 3.10+ |
| Web framework | FastAPI |
| Database | SQLite locally (WAL mode); hosted **Turso** (libSQL) in production and CI — same code path, selected by whether `TURSO_DATABASE_URL` is set |
| Scraping | `urllib` + regex — no Playwright, no Selenium, no `requests` |
| Tests | pytest (backend), Vitest (frontend) |
| Automation | GitHub Actions — weekly scrape cron, self-heal, keep-warm ping |

## Retailers Scraped

| Site | Notes |
|---|---|
| pakbyte.pk | Shopify storefront — `/collections/<slug>` pagination |
| junaidtech.pk | webx.pk Nuxt SSR — Bearer token from `__NUXT_DATA__`, POST JSON API, server-side stock filter |
| zahcomputers.pk | WooCommerce / Woodmart theme |
| techarc.pk | WooCommerce / Woodmart — flat permalinks (no `/product-category/`) |
| czone.com.pk | webx.pk Nuxt SSR — JSON-LD CollectionPage, 10 categories |
| amdhouse.pk | WooCommerce / Flatsome |
| techmatched.pk | WooCommerce / Woostify |
| rbtechngames.com | WooCommerce / Flatsome |
| redtech.pk | WooCommerce / Woodmart |

Prebuilts scraped from: zestrogaming.com, redtech.pk, techmatched.pk

Full architecture, the shared `fetch()` retry/circuit-breaker contract, and per-site parsing
detail live in `docs/scrapers-reference.md`.

## Database Design

Two-table core for parts:

- **`parts`** — one row per unique product, keyed on `(source, source_id)` where `source_id` is a
  stable slug derived from the product URL. Carries a denormalized `latest_price` (kept in sync
  with `price_log` at write time, so the listing query never needs a correlated subquery),
  `is_active`/`delisted_at` (soft-delete freshness tracking), and `name_norm` (precomputed search
  index)
- **`price_log`** — one row per scrape run per product, the source of truth for price history

Also: `shared_builds` (shareable PC build links), `prebuilts` (complete prebuilt systems),
`price_trends` (precomputed per-category/per-model aggregate price history), `scrape_runs` (one
row per source per scrape run — drives the STALE ribbon and Discord report), and
`quarantined_rows` (rows a data-quality guard rejected, kept so an over-broad rule is
discoverable instead of silently eating real products).

Specs (socket, VRAM, DDR type, wattage, etc.) are extracted automatically at upsert time via a
regex-based `spec_extractor` — no manual tagging needed.

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/parts` | List parts with filters: category, source, price range, specs, search, sort, `include_specs` |
| GET | `/api/parts/filters` | Distinct filter values for a category |
| GET | `/api/search-index` | Compact per-category index for in-browser search (ETag + 1h cache) |
| GET | `/api/stats` | Part counts by source/category, plus per-retailer scrape health |
| POST | `/api/builds/share` | Save a build, get a short code |
| GET | `/api/builds/share/{code}` | Resolve a shared build |
| GET | `/api/prebuilts` | Browse prebuilt PCs with filters |
| GET | `/api/prebuilts/{id}` | Single prebuilt detail |
| GET | `/api/trends/groups` | Precomputed price trend series by category/model |

## Running Locally

```bash
# Install dependencies (installs FastAPI, uvicorn, the libsql client for
# Turso mode, and python-dotenv for local .env loading — see requirements.txt)
pip install -r requirements.txt

# Start the API server — run from the repo root, not from backend/.
# DB_PATH resolves relative to the working directory, so starting from
# backend/ silently creates and uses an empty backend/data/ppc.db.
uvicorn backend.main:app --reload

# Run all scrapers
python run_all.py

# Run specific scrapers
python run_all.py czone zah

# Run prebuilt scrapers
python -m scrapers.prebuilts.run_prebuilts

# Tests
python -m pytest tests/
cd frontend && npm test

# Operational DB integrity check, against whatever DB the env currently points at
python scripts/check_db_integrity.py
```

By default this runs entirely against a local SQLite file at `data/ppc.db`, created by the first
scrape. Set `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` (see `.env`) to point every command —
scrapers, API server, tests — at the hosted Turso database instead; the code path is identical
either way (`db/libsql_adapter.py` makes a libsql connection look like `sqlite3`).

## Project Structure

```
backend/
  main.py              # FastAPI app entry point — CORS, GZipMiddleware, cache-control rules
  routers/
    parts.py            # /api/parts, /api/parts/filters, /api/search-index, /api/stats
    builds.py            # /api/builds/share
    prebuilts.py          # /api/prebuilts
    trends.py              # /api/trends/groups
db/
  schema.sql            # DDL for all seven tables
  tokenize.py            # the search tokenizer (mirrored in frontend/lib/search-tokenize.ts)
  libsql_adapter.py        # Turso/libSQL -> sqlite3 shim
  database.py               # all DB access; picks sqlite3 vs libsql from TURSO_DATABASE_URL
scrapers/               # 9 part retailers
  base_scraper.py       # BaseScraper: honest bot UA, fetch() retry/pacing, HostBlocked breaker
  listing_scraper.py     # ListingScraper: shared pagination template, used by 8 of 9 part scrapers
  spec_extractor.py       # regex spec extraction from product names
  health.py                 # post-scrape anomaly detection for --strict + self-heal
  czone/ zahcomputers/ junaidtech/ amdhouse/ rbtechngames/ pakbyte/ techarc/ redtech/ techmatched/
  prebuilts/              # zestro, redtech, techmatched prebuilt scrapers
scripts/
  check_db_integrity.py  # operational integrity check, safe against production
  migrations/              # one-shot migration scripts — see scripts/README.md
tests/                  # 57 files, 569 pytest tests (+ tests/api/, tests/scrapers/)
.github/workflows/      # scrape.yml, heal.yml, rerun.yml, keepwarm.yml — see docs/automation.md
run_all.py              # parts scraper orchestrator
```

## Related

- **Frontend:** [rigpk-front](https://github.com/AzaanFaisal606/rigpk-front)
- **Project hub:** [rigpk](https://github.com/AzaanFaisal606/rigpk)

## License

[MIT](LICENSE)
