# rigpk-backend

Backend for [RigPK](https://github.com/AzaanFaisal606/rigpk) — a PCPartPicker-style site for Pakistan. Scrapes PC part prices from Pakistani retailers, stores them in SQLite with full price history, and serves a REST API consumed by the Next.js frontend.

---

## What It Does

- Scrapes 5 Pakistani retailers for GPU, CPU, RAM, SSD, PSU, case, motherboard, cooling parts
- Stores ~4,800 parts with price history in SQLite
- Tracks 121 prebuilt PCs from 3 retailers
- Exposes a REST API for filtering, searching, build sharing, and prebuilt browsing

## Tech Stack

| Layer | Tech |
|---|---|
| Language | Python 3.10+ |
| Web framework | FastAPI |
| Database | SQLite (WAL mode) |
| Scraping | `urllib` + regex — no Playwright, no Selenium |
| Tests | pytest |

## Retailers Scraped

| Site | Notes |
|---|---|
| czone.com.pk | 10 categories, largest source |
| zahcomputers.pk | WooCommerce / Woodmart theme |
| junaidtech.pk | Nuxt SSR — Bearer token from `__NUXT_DATA__`, POST JSON API |
| amdhouse.pk | WooCommerce / Flatsome |
| rbtechngames.com | WooCommerce / Flatsome |

Prebuilts scraped from: zestrogaming.com, redtech.pk, techmatched.pk

## Database Design

Two-table design for parts:

- **`parts`** — one row per unique product, keyed on `(source, source_id)` where `source_id` is a stable slug derived from the product URL
- **`price_log`** — one row per scrape run per product, enabling full price history over time

Additional tables: `shared_builds` (shareable PC build links), `prebuilts` (complete prebuilt systems).

Specs (socket, VRAM, DDR type, wattage, etc.) are extracted automatically at upsert time via a regex-based `spec_extractor` — no manual tagging needed.

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/parts` | List parts with filters: category, source, price range, specs, search |
| GET | `/api/parts/filters` | Distinct filter values for a category |
| GET | `/api/stats` | Part counts by source and category |
| POST | `/api/builds/share` | Save a build, get a short code |
| GET | `/api/builds/share/{code}` | Resolve a shared build |
| GET | `/api/prebuilts` | Browse prebuilt PCs with filters |
| GET | `/api/prebuilts/{id}` | Single prebuilt detail |

## Running Locally

```bash
# Install dependencies
pip install fastapi uvicorn

# Start API server
cd backend && uvicorn main:app --reload

# Run all scrapers
python run_all.py

# Run specific scrapers
python run_all.py czone zahcomputers

# Run prebuilt scrapers
python -m scrapers.prebuilts.run_prebuilts

# Tests
python -m pytest tests/
python -m tests.test_db_integrity
```

## Project Structure

```
backend/
  main.py              # FastAPI app entry point
  routers/
    parts.py           # /api/parts + /api/stats
    builds.py          # /api/builds/share
    prebuilts.py       # /api/prebuilts
db/
  schema.sql           # SQLite DDL
  database.py          # All DB operations
scrapers/
  base_scraper.py      # Shared fetch + parse logic
  spec_extractor.py    # Regex spec extraction from product names
  czone/
  zahcomputers/
  junaidtech/
  amdhouse/
  rbtechngames/
  prebuilts/           # Prebuilt PC scrapers
tests/
  test_spec_extractor.py   # 56 unit tests
  test_db_integrity.py     # 8 DB integrity checks
run_all.py             # Scraper orchestrator
```

## Related

- **Frontend:** [rigpk-front](https://github.com/AzaanFaisal606/rigpk-front)
- **Project hub:** [rigpk](https://github.com/AzaanFaisal606/rigpk)

## License

[MIT](LICENSE)
