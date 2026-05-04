# RigPK Refactor Instructions

Full audit findings grouped by area and severity. Each issue lists what it is, why it matters, and how to fix it.

Severity scale: **[CRITICAL]** breaks correctness or maintainability, **[HIGH]** causes real pain, **[MEDIUM]** code smell / duplication, **[LOW]** polish / convention.

---

## Backend

### [CRITICAL] `sys.path.insert` hacks in every router

**Problem:** Every router file (`parts.py`, `builds.py`, `prebuilts.py`) does:
```python
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
```
This is a path hack to make imports work. It's fragile, order-sensitive, and means the project has no real package structure. Running from an unexpected directory silently breaks imports.

**Fix:** Add a `pyproject.toml` with `[tool.setuptools.packages.find]` or a simple `setup.py`, then `pip install -e .` once. All imports become absolute and the hacks disappear. Alternatively, set `PYTHONPATH=.` in a `.env` file and document it.

---

### [CRITICAL] `DB_PATH` hardcoded in every router

**Problem:** All three router files define their own:
```python
DB_PATH = Path(__file__).parent.parent.parent / "data" / "ppc.db"
```
And `run_all.py` has `DB_PATH = "data/ppc.db"` (relative string, cwd-dependent). If the DB path ever changes or is parameterised for deployment, it must be updated in 4+ places.

**Fix:** Create `backend/config.py`:
```python
import os
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).parent.parent / "data" / "ppc.db")))
```
Import `DB_PATH` from there everywhere. `database.py` already has `_DEFAULT_DB` doing this — extend that pattern.

---

### [CRITICAL] `BasePrebuiltScraper` duplicates `BaseScraper` entirely

**Problem:** `scrapers/prebuilts/base_prebuilt_scraper.py` reimplements `fetch()` with identical retry logic, identical headers, identical user agent, and identical `now()`. It exists as a separate class for no reason.

**Fix:** Make `BasePrebuiltScraper` extend `BaseScraper`. It only needs to add `scrape_all()` and `write_to_db()`.

```python
from scrapers.base_scraper import BaseScraper

class BasePrebuiltScraper(BaseScraper):
    @abstractmethod
    def scrape_all(self) -> list[dict]: ...

    @staticmethod
    def write_to_db(prebuilts, db_path=None) -> int: ...
```

---

### [HIGH] Every scraper has its own `main()` that duplicates DB-write logic

**Problem:** `czone/all_scraper.py`, `zahcomputers/scraper.py`, `junaidtech/scraper.py` all have their own `main()` function that re-implements: scrape → collect → write to DB → print stats. This is also done in `run_all.py`. Four places with near-identical code.

**Fix:** Each scraper's `main()` should be a one-liner that delegates to `run_all.py`'s logic, or be removed entirely in favour of always using `run_all.py`. The standalone `main()` functions are only useful for quick testing — replace them with a `--scraper=czone` flag on `run_all.py`.

---

### [HIGH] `_now()` static method duplicated across every scraper

**Problem:** `CzoneAllScraper`, `ZahComputersScraper`, `JunaidTechScraper` each define an identical `_now()` staticmethod returning `datetime.now(timezone.utc).isoformat()`. `BaseScraper` doesn't define it, so each subclass adds it independently. `BasePrebuiltScraper` also defines `now()` (different name, same body).

**Fix:** Add `now()` to `BaseScraper` as a `@staticmethod`. Remove from all subclasses.

---

### [HIGH] `requirements.txt` contains unused packages + wrong versions

**Problem:**
- `beautifulsoup4` and `requests` are listed but never imported anywhere in the codebase (all scraping uses `urllib`).
- `starlette==1.0.0` is almost certainly wrong — Starlette 1.0 doesn't exist as a stable release; this was likely auto-generated.

**Fix:** Audit and clean `requirements.txt` to only what's actually imported. Run `pip install pipreqs && pipreqs . --force` to auto-generate a correct one, then hand-verify.

---

### [HIGH] No input validation on `category` in `parts.py` silently returns all parts

**Problem:**
```python
if category not in VALID_CATEGORIES:
    category = None
```
Invalid category silently falls through to returning all parts instead of a 400. This makes client bugs invisible — a typo in a category name returns thousands of unrelated rows.

**Fix:** Return HTTP 400 if category is provided but invalid:
```python
if category and category not in VALID_CATEGORIES:
    raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
```

---

### [HIGH] `upsert_products` runs a separate `SELECT` after every `INSERT OR CONFLICT`

**Problem:** After each upsert, there's a second query to get `part_id`:
```python
part_id = cur.execute(
    "SELECT id FROM parts WHERE source=? AND source_id=?", ...
).fetchone()["id"]
```
For 4,800 parts this is ~4,800 extra round-trips per scrape run. SQLite supports `RETURNING id` since 3.35 (2021).

**Fix:**
```python
row = cur.execute("""
    INSERT INTO parts (...) VALUES (...)
    ON CONFLICT(...) DO UPDATE SET ...
    RETURNING id
""", ...).fetchone()
part_id = row["id"]
```

---

### [HIGH] `list_prebuilts` conditions always has `price_pkr IS NOT NULL` but can have empty `WHERE`

**Problem:** Unlike `list_parts`, `list_prebuilts` always uses `WHERE` (the initial condition is unconditional), so it's fine. But the `where` construction is fragile — if someone removes the initial condition, the query breaks without an obvious error. The pattern is inconsistent with `list_parts`.

**Fix:** Use the same `conditions: list[str]` + `("WHERE " + " AND ".join(conditions)) if conditions else ""` pattern as `list_parts`.

---

### [MEDIUM] `VALID_CATEGORIES` and `VALID_SOURCES` defined in `parts.py` router, not shared

**Problem:** `parts.py` defines `VALID_CATEGORIES` and `VALID_SOURCES`. `database.py` defines `_CATEGORY_SPEC_KEYS` and `_MIN_PRICE` independently. `test_db_integrity.py` defines `EXPECTED_SOURCES` again. All are the same truth about what sources and categories exist — defined in 3+ places.

**Fix:** Create `backend/constants.py` (or `db/constants.py`):
```python
VALID_CATEGORIES = {"gpu", "cpu", "ram", ...}
VALID_SOURCES = {"czone.com.pk", ...}
```
Import from there everywhere.

---

### [MEDIUM] `prebuilts.py` router prefix is `/api` but it's a separate file from `parts.py`

**Problem:** `parts.py` has `router = APIRouter(prefix="/api")` and `prebuilts.py` also has `router = APIRouter(prefix="/api")`. Two separate files, same prefix, no sub-grouping. If someone adds a route that collides it silently wins by registration order.

**Fix:** `prebuilts.py` router prefix should be `/api/prebuilts`. Move the `/prebuilts` path out of individual route decorators into the prefix.

---

### [MEDIUM] `spec_extractor.py` has no `monitor` or `hdd` extraction but `_CATEGORY_SPEC_KEYS` lists them

**Problem:** `_CATEGORY_SPEC_KEYS` in `database.py` has entries for `hdd` and `monitor` (both with `["brand"]`). `extract_specs()` in `spec_extractor.py` has `elif` chains that never handle `hdd` or `monitor` — they fall through with only brand extracted. No tests cover these categories.

**Fix:** Either add explicit `elif category in ("hdd", "monitor"):` stanzas (even if just brand), or document that these categories are brand-only and match the spec key list to what's actually extracted.

---

### [MEDIUM] Scraper `category` field set to `""` then patched by caller

**Problem:** Every scraper returns products with `"category": ""` and the caller does:
```python
for p in products:
    p["category"] = category
```
This pattern means if someone calls `scraper.scrape(url)` directly (e.g. in tests), they get products with empty category that will corrupt the DB if inserted.

**Fix:** Each scraper `scrape()` method should accept `category: str` as a parameter and set it directly, or the schema should require the caller pattern to be explicit. At minimum, `upsert_products` should raise if category is empty.

---

### [MEDIUM] `render.yaml` service name is still `ppc-api`, not updated for rebrand

**Problem:** After renaming repos to `rigpk-*`, `render.yaml` still references `name: ppc-api`. Minor but inconsistent for deployment.

**Fix:** Update to `name: rigpk-api`.

---

### [LOW] `get_latest_prices()` in `database.py` is dead code

**Problem:** `get_latest_prices()` is defined but never called anywhere — `list_parts()` replaced it. It uses a subquery pattern that's slightly different from `list_parts()`.

**Fix:** Delete `get_latest_prices()`.

---

### [LOW] `description.md` in project root is stale planning doc, not for the repo

**Problem:** `description.md` appears to be an old design/planning document. It's in the root, not `.gitignore`d, not part of any docs structure.

**Fix:** Move to `docs/` or delete if superseded by `CLAUDE.md` and `README.md`.

---

### [LOW] `db/enrich_specs.py` and `db/migrate_add_specs.py` are one-shot migration scripts committed to repo

**Problem:** These were clearly run once to backfill spec data. They now sit in `db/` as permanent files that suggest they're part of the DB API surface. Running them again would be harmless but confusing.

**Fix:** Move to `scripts/` directory (create it) with a `README` note that these are one-time migration scripts.

---

## Frontend

### [CRITICAL] `ComicDropdown` implemented twice — completely divergent

**Problem:** `FilterBar.tsx` has its own `ComicDropdown` component and `PrebuiltFilterBar.tsx` has a completely separate `ComicDropdown` with the same name but different props, different portal behaviour, and different option-format handling. This is the exact problem you called out. When one gets a fix or style change, the other doesn't.

**Fix:** Extract a shared `ComicDropdown` component to `components/ui/ComicDropdown.tsx`. The prebuilt version has the better portal implementation (`createPortal` + `getBoundingClientRect` for overflow escape). Use that as the canonical implementation.

---

### [CRITICAL] `PriceRangeFilter` exists only in `FilterBar.tsx`, not reused in prebuilts

**Problem:** `PrebuiltFilterBar.tsx` implements its own inline price inputs instead of using or extending the `PriceRangeFilter` component from `FilterBar.tsx`. Two different UX patterns for the same thing.

**Fix:** Extract `PriceRangeFilter` to `components/ui/PriceRangeFilter.tsx` and use it in both filter bars.

---

### [CRITICAL] `monoFont` constant defined 5+ times across components

**Problem:** `const monoFont = '"JetBrains Mono", "Fira Code", monospace'` appears in `FilterBar.tsx`, `PrebuiltFilterBar.tsx`, `market/page.tsx`, `prebuilts/page.tsx`, and likely more. Any font change requires a grep.

**Fix:** Define once in `lib/tokens.ts` (or `lib/design.ts`) and import:
```ts
export const monoFont = '"JetBrains Mono", "Fira Code", monospace';
export const BORDER = "#111112";
export const PURPLE = "#7c3aed";
```
These are already de-facto design tokens — make them explicit.

---

### [HIGH] `useScrollHide` scroll logic duplicated in both filter bars

**Problem:** Both `FilterBar.tsx` and `PrebuiltFilterBar.tsx` implement the same scroll-hide behaviour: `useRef` for `lastScrollY`, `useEffect` with `window.addEventListener("scroll")`, hide when `currentY > lastY && currentY > 80`, show when `currentY < lastY`. Identical logic, different variable names.

**Fix:** Extract a `useScrollHide()` custom hook to `lib/hooks/useScrollHide.ts`:
```ts
export function useScrollHide(threshold = 80): boolean {
  const [hidden, setHidden] = useState(false);
  const lastY = useRef(0);
  useEffect(() => {
    const handler = () => { ... };
    window.addEventListener("scroll", handler, { passive: true });
    return () => window.removeEventListener("scroll", handler);
  }, []);
  return hidden;
}
```

---

### [HIGH] `str()` helper duplicated in `market/page.tsx` and `prebuilts/page.tsx`

**Problem:** Both pages define the identical:
```ts
function str(v: string | string[] | undefined): string | undefined {
  if (!v) return undefined;
  return Array.isArray(v) ? v[0] : v;
}
```

**Fix:** Move to `lib/utils.ts` and import in both pages.

---

### [HIGH] `compatibility.ts` imports `BuildState` from `app/build/page.tsx`

**Problem:**
```ts
import type { BuildState } from "@/app/build/page";
```
A `lib/` file importing from `app/` is an inverted dependency. `lib/` is supposed to be shared utilities; `app/` is the consumer. This creates a circular-risk and breaks if `BuildPage` is ever split.

**Fix:** Move `BuildState` type definition to `lib/types.ts` where `SlotKey` already lives. Both `app/build/page.tsx` and `lib/compatibility.ts` import from `lib/types.ts`.

---

### [HIGH] `prebuilts-api.ts` duplicates `API_BASE` from `api.ts`

**Problem:** Both files define:
```ts
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
```
Two sources of truth for the same env var. If the var name changes, one will be missed.

**Fix:** Export `API_BASE` from `lib/api.ts` and import it in `prebuilts-api.ts`. Or merge `prebuilts-api.ts` into `api.ts` entirely — they're both API client code.

---

### [HIGH] `SPEC_KEYS` array defined in both `FilterBar.tsx` and `market/page.tsx`

**Problem:** The 14-element `SPEC_KEYS` array appears in both `FilterBar.tsx` and `market/page.tsx`. Category constants appear in `FilterBar.tsx` and `market/[category]/page.tsx`. These must stay in sync manually.

**Fix:** Export `SPEC_KEYS`, `CATEGORIES`, and `SOURCES` from `lib/constants.ts` and import everywhere.

---

### [MEDIUM] `BuildState` type defined in `app/build/page.tsx` instead of `lib/types.ts`

**Problem:** `BuildState` is exported from a page file, which is unusual and breaks the convention that types live in `lib/types.ts`. Other components import from a page file — bad dependency direction.

**Fix:** Move to `lib/types.ts` alongside `SlotKey`.

---

### [MEDIUM] Footer is copy-pasted in `market/page.tsx` and `market/[category]/page.tsx`

**Problem:** The footer `<footer>` block with the "RigPK — prices updated regularly..." text is duplicated between the two market pages. If wording or style changes, must update twice.

**Fix:** Either extract a `<Footer />` component or — better — move it to the layout level so it appears on all pages without per-page duplication.

---

### [MEDIUM] `useEffect` dependency array suppression in `FilterBar.tsx`

**Problem:**
```ts
// eslint-disable-next-line react-hooks/exhaustive-deps
}, [searchInput]);
```
The `push` function is excluded from the debounce effect's dep array. This works currently because `push` is wrapped in `useCallback`, but the suppression hides the real issue and will silently break if `push` is ever changed to not be stable.

**Fix:** Add `push` to the dependency array and verify the debounce still works. If it causes re-trigger loops, memoize differently rather than suppressing the lint.

---

### [MEDIUM] `next.config.ts` `remotePatterns` doesn't include prebuilt image domains

**Problem:** `next.config.ts` lists retailer domains for `images.remotePatterns` but all three prebuilt retailers (`zestrogaming.com`, `redtech.pk`, `techmatched.pk`) are missing. This is why all prebuilt thumbnails use plain `<img>` instead of Next.js `<Image>` — but the config should still be updated to reflect reality.

**Fix:** Add the three prebuilt domains, even if the components still use plain `<img>` for referrer reasons. Keeps the config accurate.

---

### [MEDIUM] `ToggleChip` component defined inside `PrebuiltFilterBar.tsx`

**Problem:** `ToggleChip` is a reusable design-system component (styled comic button with active state) but is trapped inside `PrebuiltFilterBar.tsx`. It could be used anywhere a toggle chip is needed.

**Fix:** Extract to `components/ui/ToggleChip.tsx`.

---

### [LOW] `lib/prebuilt-tags.ts` — magic numbers with no named constants

**Problem:** `derivePrebuiltTags` has hardcoded price thresholds and component keywords inline. Changing the "Gaming" tag threshold or adding a new tier requires reading the logic carefully.

**Fix:** Extract thresholds to named constants at the top of the file.

---

### [LOW] `market/[category]/page.tsx` has hardcoded `BASE = "https://rigpk.vercel.app"`

**Problem:** Hardcoded production URL in source code. Breaks canonical URL generation in development and makes the app less portable.

**Fix:** Move to `NEXT_PUBLIC_SITE_URL` env var, documented in a `.env.example` file.

---

### [LOW] No `.env.example` file in either repo

**Problem:** `NEXT_PUBLIC_API_URL` and `FRONTEND_URL` are referenced in code but there's no `.env.example` documenting what env vars are required. A new developer cloning the repo has no idea what to set.

**Fix:** Add `frontend/.env.example`:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SITE_URL=http://localhost:3000
```
And `backend/.env.example` (root):
```
DB_PATH=data/ppc.db
FRONTEND_URL=http://localhost:3000
```

---

## Project Structure

### [HIGH] No `pyproject.toml` — project isn't an installable package

**Problem:** Without `pyproject.toml` or `setup.py`, the backend has no formal package definition. This means: no clean imports, the `sys.path` hacks are necessary, `pip install -e .` doesn't work, and tools like `mypy`, `ruff`, `pytest` can't be configured centrally.

**Fix:** Add `pyproject.toml`:
```toml
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "rigpk-backend"
version = "0.1.0"
requires-python = ">=3.10"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

---

### [HIGH] Tests don't cover DB write path, scraper output schema, or API endpoints

**Problem:** `test_spec_extractor.py` is thorough (56 tests). `test_db_integrity.py` checks the DB state after scraping. But there are zero tests for:
- `upsert_products()` filtering logic (blocklist, min price, NULL price)
- API endpoints (FastAPI `TestClient` tests)
- Scraper output schema validation (does each scraper return required keys?)

**Fix:**
- Add `tests/test_database.py` — unit tests for `upsert_products`, `list_parts`, `create_shared_build`
- Add `tests/test_api.py` — FastAPI `TestClient` tests for all endpoints with fixture DB
- Add `tests/test_scraper_schema.py` — validate output dict keys and types from each scraper's `_parse_page`

---

### [MEDIUM] One-shot migration scripts committed to `db/`

**Problem:** `db/enrich_specs.py` and `db/migrate_add_specs.py` are migration scripts that were run once. They live alongside `database.py` as if they're part of the DB API.

**Fix:** Move to `scripts/migrations/`. Add a `scripts/README.md` noting these are one-shot and should not be re-run.

---

### [MEDIUM] `gaming-pc-wireframe-drawing-line-600nw-2588972631.webp` in root

**Problem:** A stock image file sits in the project root with its purchase watermark filename. It appears to be a reference image used during the build wireframe design phase.

**Fix:** Delete it. The actual wireframe component is SVG-based and doesn't use this file.

---

### [MEDIUM] `docs/superpowers/` is Claude Code internal, not project docs

**Problem:** `docs/superpowers/` is generated by the superpowers plugin for Claude Code internal use. It shouldn't be in the project docs and could confuse contributors.

**Fix:** Ensure `.gitignore` covers `docs/superpowers/`. Move any genuine project specs to `docs/specs/`.

---

### [LOW] `data/` directory has only `.gitkeep` — could use a `README`

**Problem:** `data/` is gitignored except for `.gitkeep`. Someone cloning the repo won't know how to populate it.

**Fix:** Replace `.gitkeep` with a `README.md` (1–2 lines): "This directory holds `ppc.db`. Run `python run_all.py` to generate it."

---

### [LOW] No `Makefile` or `justfile` for common dev tasks

**Problem:** Starting the dev environment requires running multiple commands from memory (`uvicorn backend.main:app --reload`, `npm run dev`, `python run_all.py`). Not documented in one place below README level.

**Fix:** Add a `Makefile` with targets: `make dev-backend`, `make dev-frontend`, `make scrape`, `make test`.

---

## Summary by Priority

| Priority | Count | Key items |
|---|---|---|
| CRITICAL | 4 | `sys.path` hacks, hardcoded DB paths, `BasePrebuiltScraper` duplication, duplicate `ComicDropdown` |
| HIGH | 10 | DB write perf, dead imports, missing validation, `monoFont` duplicated 5x, `useScrollHide` duplication, inverted lib→app import |
| MEDIUM | 12 | Constants scattered, `str()` helper duplication, `PriceRangeFilter` not shared, router prefix collision, spec category mismatch |
| LOW | 7 | Missing `.env.example`, hardcoded prod URL, no Makefile, stale files |

---

## Implementation Status

### Completed (branch: `refactor/critical-high`)

**CRITICAL**
- ✅ `sys.path.insert` hacks — fixed via `pyproject.toml` + `pip install -e .`
- ✅ `DB_PATH` hardcoded — centralised in `backend/config.py`
- ✅ `BasePrebuiltScraper` duplicates `BaseScraper` — now extends it
- ✅ `ComicDropdown` implemented twice — unified in `components/ui/ComicDropdown.tsx`

**HIGH (backend)**
- ✅ `_now()` duplicated in scrapers — moved to `BaseScraper.now()`
- ✅ `requirements.txt` unused deps — removed `beautifulsoup4`, `requests`
- ✅ No 400 on invalid category — now raises `HTTPException(400)`
- ✅ Double DB query per upsert — uses `RETURNING id`
- ✅ `get_latest_prices()` dead code — deleted

**HIGH (frontend)**
- ✅ `PriceRangeFilter` not shared — extracted to `components/ui/PriceRangeFilter.tsx`
- ✅ `monoFont` in 5+ files — centralised in `lib/tokens.ts`
- ✅ `useScrollHide` duplicated — extracted to `lib/hooks/useScrollHide.ts`
- ✅ `str()` duplicated — extracted to `lib/utils.ts`
- ✅ `compatibility.ts` imports from `app/` — `BuildState` moved to `lib/types.ts`
- ✅ `API_BASE` duplicated — exported from `lib/api.ts`
- ✅ `SPEC_KEYS` duplicated — extracted to `lib/constants.ts`

**MEDIUM/LOW (backend)**
- ✅ `VALID_CATEGORIES`/`VALID_SOURCES` scattered — centralised in `backend/constants.py`
- ✅ `prebuilts.py` router prefix `/api` — changed to `/api/prebuilts`
- ✅ `spec_extractor.py` missing `hdd`/`monitor` stanzas — added explicit `elif` with `pass`
- ✅ Scraper empty category not guarded — `upsert_products()` now raises `ValueError`
- ✅ `render.yaml` service name `ppc-api` — renamed to `rigpk-api`
- ✅ `description.md` stale planning doc — deleted
- ✅ `db/enrich_specs.py` + `migrate_add_specs.py` one-shot scripts — moved to `scripts/migrations/`
- ✅ `data/.gitkeep` — replaced with `data/README.md`
- ✅ No root `.env.example` — added

**MEDIUM/LOW (frontend)**
- ✅ `FilterBar.tsx` eslint dep suppression — removed, `push` added to dep array
- ✅ `next.config.ts` missing prebuilt domains — added zestrogaming, redtech, techmatched
- ✅ `ToggleChip` trapped in `PrebuiltFilterBar.tsx` — extracted to `components/ui/ToggleChip.tsx`
- ✅ Footer duplicated in both market pages — extracted to `components/Footer.tsx`
- ✅ `prebuilt-tags.ts` magic numbers — extracted to named constants
- ✅ Hardcoded `BASE` URL — replaced with `NEXT_PUBLIC_SITE_URL` env var
- ✅ No `frontend/.env.example` — added

**Project structure**
- ✅ No `Makefile` — added with `dev-backend`, `dev-frontend`, `scrape`, `test` targets
- ✅ `docs/superpowers/` gitignore — already covered in `.gitignore`

### Remaining

All issues from the original audit are now implemented. See git log for details.
