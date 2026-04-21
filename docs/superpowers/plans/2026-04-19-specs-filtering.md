# Specs/Tags Filtering System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `specs` JSON column to scraped parts (populated by product-name regex parsing), expose spec-based filtering through the API, and render dynamic filter dropdowns in the frontend.

**Architecture:** A standalone `scrapers/spec_extractor.py` module parses brand + category-specific attributes (socket, VRAM, DDR type, chipset, wattage, etc.) from product name strings. The DB layer calls it at upsert time and stores the result as a JSON text column. The API exposes individual spec query params and a `/filters` endpoint that returns the distinct values present in the DB for a given category. The frontend `FilterBar` renders dynamic dropdowns from those values.

**Tech Stack:** Python 3.10+ (re, json, sqlite3), FastAPI + Pydantic, Next.js (React server components + client components)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `db/schema.sql` | Modify | Add `specs TEXT` column to `parts` table |
| `db/migrate_add_specs.py` | Create | One-time ALTER TABLE migration for existing DB |
| `scrapers/spec_extractor.py` | Create | `extract_specs(name, category) -> dict` — all regex patterns |
| `tests/test_spec_extractor.py` | Create | Unit tests for every category pattern |
| `db/enrich_specs.py` | Create | Retroactively populate specs for all existing rows |
| `db/database.py` | Modify | `upsert_products` computes specs; `list_parts` gains `specs_filter`; new `get_filter_options` |
| `backend/routers/parts.py` | Modify | Spec query params on `GET /parts`; new `GET /parts/filters` |
| `frontend/lib/api.ts` | Modify | `PartSpecs`, `FilterOptions` types; `getFilterOptions`; extended `PartsParams` |
| `frontend/components/FilterBar.tsx` | Modify | Accept `filterOptions` prop; render dynamic spec dropdowns |
| `frontend/app/market/page.tsx` | Modify | Fetch `filterOptions` server-side; pass to `FilterBar` |

---

## Task 1: Add `specs` column to schema + run migration

**Files:**
- Modify: `db/schema.sql`
- Create: `db/migrate_add_specs.py`

- [ ] **Step 1: Add `specs` column to `db/schema.sql`**

Inside `CREATE TABLE IF NOT EXISTS parts`, add after the `updated_at` line:

```sql
CREATE TABLE IF NOT EXISTS parts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT    NOT NULL,
    source_id     TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    thumbnail_url TEXT,
    specs         TEXT    DEFAULT NULL,  -- JSON dict e.g. {"brand":"AMD","socket":"AM5"}
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source, source_id)
);
```

- [ ] **Step 2: Create `db/migrate_add_specs.py`**

```python
"""
One-time migration: adds `specs` TEXT column to the parts table.
Run from project root: python -m db.migrate_add_specs
Safe to re-run.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "ppc.db"


def run():
    conn = sqlite3.connect(str(DB_PATH))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(parts)")]
    if "specs" not in cols:
        conn.execute("ALTER TABLE parts ADD COLUMN specs TEXT DEFAULT NULL")
        conn.commit()
        print("Migration complete: specs column added.")
    else:
        print("Column already exists — skipping.")
    conn.close()


if __name__ == "__main__":
    run()
```

- [ ] **Step 3: Run migration**

```bash
cd /home/azaan/Documents/PPC
python -m db.migrate_add_specs
```

Expected output: `Migration complete: specs column added.`

- [ ] **Step 4: Verify column exists**

```bash
python -c "import sqlite3; c=sqlite3.connect('data/ppc.db'); print([r[1] for r in c.execute('PRAGMA table_info(parts)')])"
```

Expected: list includes `'specs'`

- [ ] **Step 5: Commit**

```bash
git add db/schema.sql db/migrate_add_specs.py
git commit -m "feat: add specs JSON column to parts table"
```

---

## Task 2: Create spec extractor module

**Files:**
- Create: `scrapers/spec_extractor.py`
- Create: `tests/test_spec_extractor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_spec_extractor.py`:

```python
import pytest
from scrapers.spec_extractor import extract_specs


# ── Brand ────────────────────────────────────────────────────────────────────

def test_brand_amd_from_ryzen():
    assert extract_specs("AMD Ryzen 5 5600 Processor", "cpu")["brand"] == "AMD"

def test_brand_amd_explicit():
    assert extract_specs("AMD Ryzen 7 7700X AM5 Box", "cpu")["brand"] == "AMD"

def test_brand_intel():
    assert extract_specs("Intel Core i5-13600K LGA1700", "cpu")["brand"] == "Intel"

def test_brand_sapphire_gpu():
    assert extract_specs("Sapphire PULSE RX 6600 8GB GDDR6", "gpu")["brand"] == "Sapphire"

def test_brand_asus():
    assert extract_specs("ASUS ROG STRIX RTX 4070 12GB GDDR6X", "gpu")["brand"] == "ASUS"

def test_brand_gskill():
    assert extract_specs("G.Skill Ripjaws V 16GB DDR4 3200MHz", "ram")["brand"] == "G.Skill"

def test_brand_coolermaster():
    assert extract_specs("Cooler Master MasterLiquid 240L AIO", "cooling")["brand"] == "Cooler Master"

def test_brand_wd():
    assert extract_specs("WD Blue SN580 1TB NVMe SSD", "ssd")["brand"] == "WD"

def test_brand_missing():
    assert "brand" not in extract_specs("Generic No Name 500W PSU", "psu")


# ── CPU socket ───────────────────────────────────────────────────────────────

def test_cpu_socket_am5():
    assert extract_specs("AMD Ryzen 5 7600 AM5 Processor", "cpu")["socket"] == "AM5"

def test_cpu_socket_am4():
    assert extract_specs("AMD Ryzen 5 5600X AM4", "cpu")["socket"] == "AM4"

def test_cpu_socket_lga1700():
    assert extract_specs("Intel Core i5-12400 LGA1700", "cpu")["socket"] == "LGA1700"

def test_cpu_socket_lga1700_with_space():
    assert extract_specs("Intel Core i7-12700K LGA 1700", "cpu")["socket"] == "LGA1700"

def test_cpu_socket_lga1851():
    assert extract_specs("Intel Core Ultra 9 285K LGA 1851", "cpu")["socket"] == "LGA1851"

def test_cpu_no_socket():
    assert "socket" not in extract_specs("Intel Core i5-13600K Desktop Processor", "cpu")


# ── GPU VRAM ─────────────────────────────────────────────────────────────────

def test_gpu_vram_with_gddr():
    assert extract_specs("Sapphire PULSE AMD Radeon RX 6400 4GB GDDR6", "gpu")["vram"] == "4GB"

def test_gpu_vram_12gb():
    assert extract_specs("ASUS ROG STRIX RTX 4070 12GB GDDR6X", "gpu")["vram"] == "12GB"

def test_gpu_vram_8gb_no_gddr():
    assert extract_specs("MSI GeForce RTX 4060 8GB Gaming X", "gpu")["vram"] == "8GB"

def test_gpu_no_vram():
    assert "vram" not in extract_specs("ASUS GeForce GT 1030 Graphics Card", "gpu")


# ── RAM ──────────────────────────────────────────────────────────────────────

def test_ram_ddr4():
    assert extract_specs("Corsair Vengeance LPX 16GB DDR4 3200MHz", "ram")["ddr_type"] == "DDR4"

def test_ram_ddr5():
    assert extract_specs("G.Skill Trident Z5 32GB DDR5 6000MHz", "ram")["ddr_type"] == "DDR5"

def test_ram_speed_mhz():
    assert extract_specs("Kingston 16GB DDR4 3200MHz", "ram")["speed"] == "3200MHz"

def test_ram_speed_from_ddr_dash():
    assert extract_specs("Corsair 32GB DDR5-5600 CL40", "ram")["speed"] == "5600MHz"

def test_ram_no_speed():
    result = extract_specs("Corsair Vengeance 8GB DDR4", "ram")
    assert "speed" not in result


# ── Motherboard ──────────────────────────────────────────────────────────────

def test_mobo_socket_am5():
    assert extract_specs("MSI MAG B650 TOMAHAWK WIFI AM5", "motherboard")["socket"] == "AM5"

def test_mobo_socket_lga1700():
    assert extract_specs("ASUS PRIME Z790-P LGA1700", "motherboard")["socket"] == "LGA1700"

def test_mobo_chipset_b650():
    assert extract_specs("Gigabyte B650M DS3H AM5", "motherboard")["chipset"] == "B650M"

def test_mobo_chipset_z790():
    assert extract_specs("MSI MEG Z790 ACE LGA1700", "motherboard")["chipset"] == "Z790"

def test_mobo_chipset_x670e():
    assert extract_specs("ASUS ROG CROSSHAIR X670E HERO AM5", "motherboard")["chipset"] == "X670E"

def test_mobo_no_chipset_false_positive():
    result = extract_specs("Samsung 870 EVO 500GB SATA SSD", "motherboard")
    assert "chipset" not in result


# ── PSU ──────────────────────────────────────────────────────────────────────

def test_psu_wattage():
    assert extract_specs("Seasonic Focus GX 750W 80 Plus Gold", "psu")["wattage"] == "750W"

def test_psu_rating_gold():
    assert extract_specs("Seasonic Focus GX 750W 80 Plus Gold", "psu")["rating"] == "80+ Gold"

def test_psu_rating_bronze():
    assert extract_specs("Corsair CV650 650W 80 Plus Bronze", "psu")["rating"] == "80+ Bronze"

def test_psu_rating_platinum():
    assert extract_specs("be quiet! Straight Power 12 850W 80 Plus Platinum", "psu")["rating"] == "80+ Platinum"

def test_psu_no_rating():
    result = extract_specs("Generic 500W Power Supply", "psu")
    assert "rating" not in result


# ── Case ─────────────────────────────────────────────────────────────────────

def test_case_atx():
    assert extract_specs("Fractal Design Meshify C ATX Mid-Tower Case", "case")["form_factor"] == "ATX"

def test_case_matx():
    assert extract_specs("NZXT H5 Flow Micro-ATX Mid Tower", "case")["form_factor"] == "Micro-ATX"

def test_case_matx_short():
    assert extract_specs("Cooler Master MasterBox Q300L mATX Case", "case")["form_factor"] == "Micro-ATX"

def test_case_mini_itx():
    assert extract_specs("Lian Li A4-H2O Mini-ITX Case", "case")["form_factor"] == "Mini-ITX"

def test_case_no_form_factor():
    result = extract_specs("Generic PC Case Black", "case")
    assert "form_factor" not in result


# ── Cooling ──────────────────────────────────────────────────────────────────

def test_cooling_aio():
    assert extract_specs("Cooler Master MasterLiquid 240L AIO Liquid Cooler", "cooling")["type"] == "AIO"

def test_cooling_aio_size():
    result = extract_specs("Cooler Master MasterLiquid 240L AIO 240mm", "cooling")
    assert result["type"] == "AIO"
    assert result["aio_size"] == "240mm"

def test_cooling_aio_360():
    result = extract_specs("NZXT Kraken 360 RGB AIO 360mm", "cooling")
    assert result["aio_size"] == "360mm"

def test_cooling_air():
    assert extract_specs("Noctua NH-D15 CPU Air Cooler Dual Tower", "cooling")["type"] == "Air"

def test_cooling_fan():
    assert extract_specs("Thermalright TL-C12C 120mm Case Fan", "cooling")["type"] == "Fan/Accessory"

def test_cooling_fan_size():
    result = extract_specs("Thermalright TL-C12C 120mm Case Fan", "cooling")
    assert result.get("fan_size") == "120mm"

def test_cooling_aio_no_fan_size():
    result = extract_specs("Cooler Master MasterLiquid 240L AIO 240mm", "cooling")
    assert "fan_size" not in result


# ── SSD ──────────────────────────────────────────────────────────────────────

def test_ssd_nvme():
    assert extract_specs("Samsung 980 Pro 1TB NVMe SSD", "ssd")["interface"] == "NVMe"

def test_ssd_sata():
    assert extract_specs("Samsung 870 EVO 500GB SATA SSD", "ssd")["interface"] == "SATA"

def test_ssd_m2_sata():
    assert extract_specs("WD Blue SA510 500GB M.2 SATA SSD", "ssd")["interface"] == "M.2 SATA"

def test_ssd_capacity_tb():
    assert extract_specs("Samsung 980 Pro 1TB NVMe SSD", "ssd")["capacity"] == "1TB"

def test_ssd_capacity_gb():
    assert extract_specs("Samsung 870 EVO 500GB SATA SSD", "ssd")["capacity"] == "500GB"

def test_ssd_capacity_2tb():
    assert extract_specs("WD Black SN850X 2TB NVMe", "ssd")["capacity"] == "2TB"


# ── Category isolation ───────────────────────────────────────────────────────

def test_gpu_does_not_extract_socket():
    result = extract_specs("ASUS ROG STRIX RTX 4070 12GB AM5", "gpu")
    assert "socket" not in result

def test_ram_does_not_extract_vram():
    result = extract_specs("Corsair 16GB DDR5 6000MHz", "ram")
    assert "vram" not in result

def test_empty_name():
    assert extract_specs("", "cpu") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/azaan/Documents/PPC
python -m pytest tests/test_spec_extractor.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'scrapers.spec_extractor'`

- [ ] **Step 3: Create `scrapers/spec_extractor.py`**

```python
from __future__ import annotations
import re
from typing import Optional

# Priority-ordered brand list: longer/more-specific first to prevent
# "WD" matching inside "ADATA", "Radeon"→AMD before bare "AMD", etc.
_BRANDS: list[tuple[str, str]] = [
    ("cooler master",   "Cooler Master"),
    ("be quiet!",       "be quiet!"),
    ("lian li",         "Lian Li"),
    ("g.skill",         "G.Skill"),
    ("g skill",         "G.Skill"),
    ("western digital", "WD"),
    ("thermalright",    "Thermalright"),
    ("thermaltake",     "Thermaltake"),
    ("powercolor",      "PowerColor"),
    ("super flower",    "Super Flower"),
    ("id-cooling",      "ID-Cooling"),
    ("teamgroup",       "TeamGroup"),
    ("viewsonic",       "ViewSonic"),
    ("kingston",        "Kingston"),
    ("samsung",         "Samsung"),
    ("seagate",         "Seagate"),
    ("gigabyte",        "Gigabyte"),
    ("sapphire",        "Sapphire"),
    ("asrock",          "ASRock"),
    ("seasonic",        "Seasonic"),
    ("corsair",         "Corsair"),
    ("fractal",         "Fractal"),
    ("phanteks",        "Phanteks"),
    ("deepcool",        "DeepCool"),
    ("gamemax",         "GameMax"),
    ("twinmos",         "TwinMOS"),
    ("hiksemi",         "HikSemi"),
    ("gainward",        "Gainward"),
    ("crucial",         "Crucial"),
    ("hyperx",          "HyperX"),
    ("patriot",         "Patriot"),
    ("apacer",          "Apacer"),
    ("lexar",           "Lexar"),
    ("noctua",          "Noctua"),
    ("cougar",          "Cougar"),
    ("antec",           "Antec"),
    ("radeon",          "AMD"),       # "Radeon" implies AMD GPU
    ("ryzen",           "AMD"),       # "Ryzen" implies AMD CPU
    ("geforce",         "NVIDIA"),    # "GeForce" implies NVIDIA GPU
    ("zotac",           "ZOTAC"),
    ("palit",           "Palit"),
    ("arktek",          "Arktek"),
    ("adata",           "ADATA"),
    ("dahua",           "Dahua"),
    ("hikvision",       "Hikvision"),
    ("intel",           "Intel"),
    ("nvidia",          "NVIDIA"),
    ("asus",            "ASUS"),
    ("nzxt",            "NZXT"),
    ("benq",            "BenQ"),
    ("dell",            "Dell"),
    ("lenovo",          "Lenovo"),
    ("philips",         "Philips"),
    ("iiyama",          "iiyama"),
    ("amd",             "AMD"),
    ("msi",             "MSI"),
    ("xpg",             "XPG"),
    ("xfx",             "XFX"),
    ("pny",             "PNY"),
    ("aoc",             "AOC"),
    ("wd",              "WD"),        # after "western digital"
    ("lg",              "LG"),
]

_SHORT_BRAND_RE: dict[str, re.Pattern] = {
    s: re.compile(rf'\b{re.escape(s)}\b', re.IGNORECASE)
    for s, _ in _BRANDS if len(s) <= 3
}


def _extract_brand(name: str) -> Optional[str]:
    lower = name.lower()
    for match_str, canonical in _BRANDS:
        if len(match_str) <= 3:
            if _SHORT_BRAND_RE[match_str].search(lower):
                return canonical
        else:
            if match_str in lower:
                return canonical
    return None


# ── CPU ──────────────────────────────────────────────────────────────────────

_SOCKET_RE = re.compile(r'\b(AM[45]|LGA\s?1[0-9]{3,4})\b', re.IGNORECASE)


def _extract_socket(name: str) -> Optional[str]:
    m = _SOCKET_RE.search(name)
    if m:
        return m.group(1).replace(" ", "").upper()
    return None


# ── GPU ──────────────────────────────────────────────────────────────────────

_VRAM_GDDR_RE = re.compile(r'(\d+)\s*GB\s+GDDR\d*', re.IGNORECASE)
_VRAM_FALLBACK_RE = re.compile(r'\b(\d+)\s*GB\b', re.IGNORECASE)
_VALID_VRAM = {2, 4, 6, 8, 10, 12, 16, 20, 24, 32}


def _extract_vram(name: str) -> Optional[str]:
    m = _VRAM_GDDR_RE.search(name)
    if m:
        return f"{m.group(1)}GB"
    m = _VRAM_FALLBACK_RE.search(name)
    if m:
        gb = int(m.group(1))
        if gb in _VALID_VRAM:
            return f"{gb}GB"
    return None


# ── RAM ──────────────────────────────────────────────────────────────────────

_DDR_TYPE_RE = re.compile(r'\b(LP?DDR[45]X?)\b', re.IGNORECASE)
_RAM_SPEED_DDR_RE = re.compile(r'DDR[45]-?(\d{4,5})', re.IGNORECASE)
_RAM_SPEED_MHZ_RE = re.compile(r'(\d{4,5})\s*(?:MHz|MT/s)', re.IGNORECASE)


def _extract_ddr_type(name: str) -> Optional[str]:
    m = _DDR_TYPE_RE.search(name)
    return m.group(1).upper() if m else None


def _extract_ram_speed(name: str) -> Optional[str]:
    m = _RAM_SPEED_DDR_RE.search(name)
    if m:
        return f"{m.group(1)}MHz"
    m = _RAM_SPEED_MHZ_RE.search(name)
    if m:
        speed = int(m.group(1))
        if 1600 <= speed <= 12000:
            return f"{speed}MHz"
    return None


# ── Motherboard ───────────────────────────────────────────────────────────────

_CHIPSET_RE = re.compile(r'\b([ABXHZ]\d{3}[EFMKPS]?)\b', re.IGNORECASE)
_VALID_CHIPSET_PREFIXES = (
    'A5', 'A6', 'B5', 'B6', 'B7', 'X5', 'X6', 'X8',
    'H6', 'H7', 'Z6', 'Z7', 'Z8',
)


def _extract_chipset(name: str) -> Optional[str]:
    for m in _CHIPSET_RE.finditer(name):
        cs = m.group(1).upper()
        if any(cs.startswith(p) for p in _VALID_CHIPSET_PREFIXES):
            return cs
    return None


# ── PSU ───────────────────────────────────────────────────────────────────────

_PSU_WATTS_RE = re.compile(r'\b(\d{3,4})W\b', re.IGNORECASE)
_PSU_RATING_RE = re.compile(
    r'80[\s]*[+]?\s*[Pp]lus\s+(Bronze|Silver|Gold|Platinum|Titanium|White)',
    re.IGNORECASE,
)


def _extract_wattage(name: str) -> Optional[str]:
    m = _PSU_WATTS_RE.search(name)
    if m:
        w = int(m.group(1))
        if 300 <= w <= 2000:
            return f"{w}W"
    return None


def _extract_psu_rating(name: str) -> Optional[str]:
    m = _PSU_RATING_RE.search(name)
    if m:
        return f"80+ {m.group(1).capitalize()}"
    return None


# ── Case ──────────────────────────────────────────────────────────────────────

def _extract_form_factor(name: str) -> Optional[str]:
    lower = name.lower()
    if re.search(r'\bmini[-\s]?itx\b', lower):
        return "Mini-ITX"
    if re.search(r'\bmicro[-\s]?atx\b|\bm-atx\b|\bmatx\b', lower):
        return "Micro-ATX"
    if re.search(r'\be[-\s]?atx\b', lower):
        return "E-ATX"
    if re.search(r'\batx\b', lower):
        return "ATX"
    if re.search(r'\bitx\b', lower):
        return "ITX"
    return None


# ── Cooling ───────────────────────────────────────────────────────────────────

_COOLING_SIZE_RE = re.compile(
    r'\b(80|92|120|140|200|240|280|360|420)\s*mm\b', re.IGNORECASE
)


def _extract_cooling_type(name: str) -> Optional[str]:
    lower = name.lower()
    if re.search(r'\b(aio|liquid\s+cool|water\s+cool|hydro|all[\s-]in[\s-]one)\b', lower):
        return "AIO"
    if re.search(r'\b(air\s+cool|heatsink|cpu\s+cooler|tower\s+cool|air\s+tower)\b', lower):
        return "Air"
    if re.search(r'\b(case\s+fan|argb\s+fan|rgb\s+fan|thermal\s+paste|thermal\s+pad)\b', lower):
        return "Fan/Accessory"
    return None


def _extract_cooling_size(name: str) -> Optional[str]:
    m = _COOLING_SIZE_RE.search(name)
    return f"{m.group(1)}mm" if m else None


# ── SSD ───────────────────────────────────────────────────────────────────────

_SSD_CAP_TB_RE = re.compile(r'\b(\d+(?:\.\d+)?)\s*TB\b', re.IGNORECASE)
_SSD_CAP_GB_RE = re.compile(r'\b(\d+)\s*GB\b', re.IGNORECASE)
_VALID_SSD_GB = {64, 128, 240, 256, 480, 500, 512, 960, 1000}


def _extract_ssd_interface(name: str) -> Optional[str]:
    lower = name.lower()
    if 'nvme' in lower:
        return "NVMe"
    if 'm.2' in lower:
        return "M.2 SATA"
    if 'sata' in lower:
        return "SATA"
    return None


def _extract_ssd_capacity(name: str) -> Optional[str]:
    m = _SSD_CAP_TB_RE.search(name)
    if m:
        tb = float(m.group(1))
        if 0.5 <= tb <= 20:
            return f"{m.group(1)}TB"
    m = _SSD_CAP_GB_RE.search(name)
    if m:
        gb = int(m.group(1))
        if gb in _VALID_SSD_GB:
            return f"{gb}GB"
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def extract_specs(name: str, category: str) -> dict:
    """
    Parse a product name and return a specs dict for the given category.
    Always attempts brand extraction. Returns {} if nothing found.
    """
    if not name:
        return {}

    specs: dict = {}

    brand = _extract_brand(name)
    if brand:
        specs["brand"] = brand

    if category == "cpu":
        s = _extract_socket(name)
        if s:
            specs["socket"] = s

    elif category == "gpu":
        v = _extract_vram(name)
        if v:
            specs["vram"] = v

    elif category == "ram":
        d = _extract_ddr_type(name)
        if d:
            specs["ddr_type"] = d
        sp = _extract_ram_speed(name)
        if sp:
            specs["speed"] = sp

    elif category == "motherboard":
        s = _extract_socket(name)
        if s:
            specs["socket"] = s
        cs = _extract_chipset(name)
        if cs:
            specs["chipset"] = cs

    elif category == "psu":
        w = _extract_wattage(name)
        if w:
            specs["wattage"] = w
        r = _extract_psu_rating(name)
        if r:
            specs["rating"] = r

    elif category == "case":
        ff = _extract_form_factor(name)
        if ff:
            specs["form_factor"] = ff

    elif category == "cooling":
        ct = _extract_cooling_type(name)
        if ct:
            specs["type"] = ct
        size = _extract_cooling_size(name)
        if size:
            if ct == "AIO":
                specs["aio_size"] = size
            else:
                specs["fan_size"] = size

    elif category == "ssd":
        iface = _extract_ssd_interface(name)
        if iface:
            specs["interface"] = iface
        cap = _extract_ssd_capacity(name)
        if cap:
            specs["capacity"] = cap

    return specs
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd /home/azaan/Documents/PPC
python -m pytest tests/test_spec_extractor.py -v
```

Expected: All tests PASS. If any fail, adjust the regex patterns in `scrapers/spec_extractor.py` until they do.

- [ ] **Step 5: Commit**

```bash
git add scrapers/spec_extractor.py tests/test_spec_extractor.py
git commit -m "feat: add spec extractor with name-parsing patterns for all categories"
```

---

## Task 3: Retroactive enrichment of existing parts

**Files:**
- Create: `db/enrich_specs.py`

- [ ] **Step 1: Create `db/enrich_specs.py`**

```python
"""
Retroactive spec enrichment — populates specs for all existing parts rows.
Run from project root: python -m db.enrich_specs
Safe to re-run (idempotent).
"""
from __future__ import annotations
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scrapers.spec_extractor import extract_specs

DB_PATH = Path(__file__).parent.parent / "data" / "ppc.db"


def run():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, name, category FROM parts ORDER BY category, id"
    ).fetchall()

    print(f"Processing {len(rows)} parts...")

    updates: list[tuple[str | None, int]] = []
    stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "with_specs": 0, "with_brand": 0})

    for row in rows:
        cat = row["category"]
        specs = extract_specs(row["name"], cat)
        specs_json = json.dumps(specs, ensure_ascii=False) if specs else None
        updates.append((specs_json, row["id"]))
        stats[cat]["total"] += 1
        if specs:
            stats[cat]["with_specs"] += 1
        if specs.get("brand"):
            stats[cat]["with_brand"] += 1

    conn.executemany("UPDATE parts SET specs = ? WHERE id = ?", updates)
    conn.commit()
    conn.close()

    print(f"\nEnrichment complete. Coverage report:")
    print(f"{'Category':15s} {'Total':>7} {'With Specs':>12} {'With Brand':>12}")
    print("-" * 50)
    for cat in sorted(stats):
        s = stats[cat]
        total = s["total"]
        print(
            f"{cat:15s} {total:>7} "
            f"{s['with_specs']:>7} ({s['with_specs']/total*100:3.0f}%)"
            f"{s['with_brand']:>7} ({s['with_brand']/total*100:3.0f}%)"
        )


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run enrichment**

```bash
cd /home/azaan/Documents/PPC
python -m db.enrich_specs
```

Expected: coverage report printed per category. CPU/GPU/RAM/Motherboard/PSU should show >80% `with_specs`.

- [ ] **Step 3: Spot-check a few rows**

```bash
python -c "
import sqlite3, json
c = sqlite3.connect('data/ppc.db')
rows = c.execute(\"SELECT name, category, specs FROM parts WHERE specs IS NOT NULL LIMIT 8\").fetchall()
for r in rows:
    print(r[1], '|', json.loads(r[2]), '|', r[0][:50])
"
```

Expected: Each row shows a parsed `specs` dict with at least `brand`.

- [ ] **Step 4: Commit**

```bash
git add db/enrich_specs.py
git commit -m "feat: add retroactive spec enrichment script"
```

---

## Task 4: Update database layer

**Files:**
- Modify: `db/database.py`

- [ ] **Step 1: Add imports at top of `db/database.py`**

Add after the existing imports (after `from typing import Optional`):

```python
import json
from scrapers.spec_extractor import extract_specs
```

- [ ] **Step 2: Update `upsert_products()` to compute and store specs**

Replace the existing `upsert_products` method body (lines 64–106) with:

```python
def upsert_products(self, products: list[dict]) -> int:
    """
    Upsert a list of scraped products and log their prices.
    Returns the number of price_log rows inserted.
    """
    inserted = 0
    cur = self._conn.cursor()
    for p in products:
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
```

- [ ] **Step 3: Update `list_parts()` to accept `specs_filter` and return `specs`**

Replace the existing `list_parts` method (lines 138–203) with:

```python
_VALID_SPEC_KEYS = frozenset({
    "brand", "socket", "vram", "ddr_type", "speed", "chipset",
    "wattage", "rating", "form_factor", "type", "aio_size",
    "fan_size", "interface", "capacity",
})


def list_parts(
    self,
    *,
    category: Optional[str] = None,
    source: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    specs_filter: Optional[dict] = None,
    sort: str = "price_asc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """
    Return (items, total) for the market listing page.
    Items have the latest price per part. NULL-price rows sort last.
    specs_filter: e.g. {"brand": "AMD", "socket": "AM5"}
    """
    conditions: list[str] = []
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
    if specs_filter:
        for key, value in specs_filter.items():
            if key in _VALID_SPEC_KEYS:
                conditions.append("json_extract(p.specs, ?) = ?")
                params.extend([f"$.{key}", value])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    order = (
        "ORDER BY pl.price_pkr IS NULL, pl.price_pkr ASC"
        if sort == "price_asc"
        else "ORDER BY pl.price_pkr IS NULL, pl.price_pkr DESC"
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
```

Note: `_VALID_SPEC_KEYS` is a module-level constant — place it just before the `Database` class definition.

- [ ] **Step 4: Add `get_filter_options()` method to `Database` class**

Add after the `list_parts` method, before `get_price_history`:

```python
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
```

Note: `_CATEGORY_SPEC_KEYS` is a module-level constant — place it just before or after `_VALID_SPEC_KEYS`.

- [ ] **Step 5: Verify DB layer works**

```bash
cd /home/azaan/Documents/PPC
python -c "
from db.database import get_db
with get_db() as db:
    opts = db.get_filter_options('cpu')
    print('CPU filter options:', opts)
    items, total = db.list_parts(category='cpu', specs_filter={'socket': 'AM5'}, limit=3)
    print(f'AM5 CPUs: {total} total, first 3:', [i['name'][:40] for i in items])
"
```

Expected: `cpu` options dict with `brand` and `socket` keys. AM5 filter returns >0 results.

- [ ] **Step 6: Commit**

```bash
git add db/database.py
git commit -m "feat: integrate spec extraction into upsert, add spec filtering and filter options to DB layer"
```

---

## Task 5: Update FastAPI backend

**Files:**
- Modify: `backend/routers/parts.py`

- [ ] **Step 1: Replace `backend/routers/parts.py` with updated version**

```python
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from db.database import get_db

router = APIRouter(prefix="/api")

DB_PATH = Path(__file__).parent.parent.parent / "data" / "ppc.db"

VALID_CATEGORIES = {
    "gpu", "cpu", "ram", "ssd", "hdd", "psu",
    "case", "motherboard", "cooling", "monitor",
}
VALID_SOURCES = {
    "czone.com.pk", "zahcomputers.pk", "amdhouse.pk",
    "rbtechngames.com", "junaidtech.pk",
}


class PartItem(BaseModel):
    id: int
    source: str
    name: str
    category: str
    url: str
    thumbnail_url: Optional[str]
    price_pkr: Optional[int]
    specs: Optional[dict[str, Any]] = None


class PartsResponse(BaseModel):
    items: list[PartItem]
    total: int


@router.get("/stats")
def get_stats():
    with get_db(DB_PATH) as db:
        return db.stats()


@router.get("/parts/filters")
def get_filter_options(category: str = Query(...)):
    if category not in VALID_CATEGORIES:
        return {}
    with get_db(DB_PATH) as db:
        return db.get_filter_options(category)


@router.get("/parts", response_model=PartsResponse)
def get_parts(
    category:    Optional[str] = Query(None),
    source:      Optional[str] = Query(None),
    min_price:   Optional[int] = Query(None, ge=0),
    max_price:   Optional[int] = Query(None, ge=0),
    sort:        str           = Query("price_asc", pattern="^price_(asc|desc)$"),
    limit:       int           = Query(50, ge=1, le=100),
    offset:      int           = Query(0, ge=0),
    brand:       Optional[str] = Query(None),
    socket:      Optional[str] = Query(None),
    vram:        Optional[str] = Query(None),
    ddr_type:    Optional[str] = Query(None),
    speed:       Optional[str] = Query(None),
    chipset:     Optional[str] = Query(None),
    wattage:     Optional[str] = Query(None),
    rating:      Optional[str] = Query(None),
    form_factor: Optional[str] = Query(None),
    cooling_type: Optional[str] = Query(None, alias="type"),
    aio_size:    Optional[str] = Query(None),
    fan_size:    Optional[str] = Query(None),
    interface:   Optional[str] = Query(None),
    capacity:    Optional[str] = Query(None),
):
    if category not in VALID_CATEGORIES:
        category = None
    if source not in VALID_SOURCES:
        source = None

    raw_spec_filters = {
        "brand": brand, "socket": socket, "vram": vram,
        "ddr_type": ddr_type, "speed": speed, "chipset": chipset,
        "wattage": wattage, "rating": rating, "form_factor": form_factor,
        "type": cooling_type, "aio_size": aio_size, "fan_size": fan_size,
        "interface": interface, "capacity": capacity,
    }
    specs_filter = {k: v for k, v in raw_spec_filters.items() if v is not None} or None

    with get_db(DB_PATH) as db:
        items, total = db.list_parts(
            category=category,
            source=source,
            min_price=min_price,
            max_price=max_price,
            specs_filter=specs_filter,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    parsed_items = []
    for item in items:
        specs_raw = item.pop("specs", None)
        item["specs"] = json.loads(specs_raw) if specs_raw else None
        parsed_items.append(item)

    return {"items": parsed_items, "total": total}
```

- [ ] **Step 2: Start backend and test endpoints**

```bash
cd /home/azaan/Documents/PPC/backend
uvicorn main:app --reload --port 8000 &
sleep 2
curl -s "http://localhost:8000/api/parts/filters?category=cpu" | python -m json.tool
```

Expected: JSON with `brand` and `socket` lists.

```bash
curl -s "http://localhost:8000/api/parts?category=cpu&socket=AM5&limit=3" | python -m json.tool | grep -E '"name"|"socket"'
```

Expected: 3 CPU parts, all with AM5 in their name. `specs.socket` = "AM5".

- [ ] **Step 3: Kill dev server**

```bash
kill %1 2>/dev/null || pkill -f "uvicorn main:app" || true
```

- [ ] **Step 4: Commit**

```bash
git add backend/routers/parts.py
git commit -m "feat: add spec filter params and /parts/filters endpoint to API"
```

---

## Task 6: Update frontend types and API client

**Files:**
- Modify: `frontend/lib/api.ts`

- [ ] **Step 1: Replace `frontend/lib/api.ts` with updated version**

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface Stats {
  total_parts: number;
  total_price_rows: number;
  by_source: Record<string, number>;
  by_category: Record<string, number>;
}

export async function getStats(): Promise<Stats | null> {
  try {
    const res = await fetch(`${API_BASE}/api/stats`, { next: { revalidate: 60 } });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export interface PartSpecs {
  brand?: string;
  socket?: string;
  vram?: string;
  ddr_type?: string;
  speed?: string;
  chipset?: string;
  wattage?: string;
  rating?: string;
  form_factor?: string;
  type?: string;
  aio_size?: string;
  fan_size?: string;
  interface?: string;
  capacity?: string;
}

export interface FilterOptions {
  brand?: string[];
  socket?: string[];
  vram?: string[];
  ddr_type?: string[];
  speed?: string[];
  chipset?: string[];
  wattage?: string[];
  rating?: string[];
  form_factor?: string[];
  type?: string[];
  aio_size?: string[];
  fan_size?: string[];
  interface?: string[];
  capacity?: string[];
}

export interface Part {
  id: number;
  source: string;
  name: string;
  category: string;
  url: string;
  thumbnail_url: string | null;
  price_pkr: number | null;
  specs: PartSpecs | null;
}

export interface PartsResult {
  items: Part[];
  total: number;
}

export interface PartsParams {
  category?: string;
  source?: string;
  min_price?: number;
  max_price?: number;
  sort?: "price_asc" | "price_desc";
  limit?: number;
  offset?: number;
  brand?: string;
  socket?: string;
  vram?: string;
  ddr_type?: string;
  speed?: string;
  chipset?: string;
  wattage?: string;
  rating?: string;
  form_factor?: string;
  type?: string;
  aio_size?: string;
  fan_size?: string;
  interface?: string;
  capacity?: string;
}

export async function getParts(params: PartsParams = {}): Promise<PartsResult> {
  const query = new URLSearchParams();
  const keys: (keyof PartsParams)[] = [
    "category", "source", "min_price", "max_price", "sort", "limit", "offset",
    "brand", "socket", "vram", "ddr_type", "speed", "chipset", "wattage",
    "rating", "form_factor", "type", "aio_size", "fan_size", "interface", "capacity",
  ];
  for (const key of keys) {
    const val = params[key];
    if (val !== undefined && val !== null && val !== "") {
      query.set(key, String(val));
    }
  }
  try {
    const res = await fetch(`${API_BASE}/api/parts?${query}`, {
      next: { revalidate: 30 },
    });
    if (!res.ok) return { items: [], total: 0 };
    return res.json();
  } catch {
    return { items: [], total: 0 };
  }
}

export async function getFilterOptions(category: string): Promise<FilterOptions> {
  try {
    const res = await fetch(
      `${API_BASE}/api/parts/filters?category=${category}`,
      { next: { revalidate: 300 } },
    );
    if (!res.ok) return {};
    return res.json();
  } catch {
    return {};
  }
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /home/azaan/Documents/PPC/frontend
npx tsc --noEmit 2>&1 | head -30
```

Expected: No errors related to `api.ts`. (Other pre-existing errors unrelated to this change are OK.)

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/api.ts
git commit -m "feat: add PartSpecs, FilterOptions types and getFilterOptions to API client"
```

---

## Task 7: Update FilterBar with dynamic spec dropdowns

**Files:**
- Modify: `frontend/components/FilterBar.tsx`
- Modify: `frontend/app/market/page.tsx`

- [ ] **Step 1: Replace `frontend/components/FilterBar.tsx` with updated version**

```tsx
"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import type { FilterOptions } from "@/lib/api";

const CATEGORIES = [
  "gpu", "cpu", "ram", "ssd", "hdd",
  "psu", "case", "motherboard", "cooling", "monitor",
];

const SOURCES = [
  { key: "czone.com.pk",     label: "CZone" },
  { key: "zahcomputers.pk",  label: "Zah Computers" },
  { key: "amdhouse.pk",      label: "AMD House" },
  { key: "rbtechngames.com", label: "RB Tech" },
  { key: "junaidtech.pk",    label: "Junaid Tech" },
];

const SPEC_LABELS: Record<string, string> = {
  brand:       "Brand",
  socket:      "Socket",
  vram:        "VRAM",
  ddr_type:    "DDR Type",
  speed:       "Speed",
  chipset:     "Chipset",
  wattage:     "Wattage",
  rating:      "80+ Rating",
  form_factor: "Form Factor",
  type:        "Cooler Type",
  aio_size:    "AIO Size",
  fan_size:    "Fan Size",
  interface:   "Interface",
  capacity:    "Capacity",
};

const selectStyle: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  color: "var(--text)",
  borderRadius: "6px",
  padding: "6px 10px",
  fontSize: "0.78rem",
  fontFamily: '"JetBrains Mono", "Fira Code", monospace',
  letterSpacing: "0.02em",
  cursor: "pointer",
  outline: "none",
  minWidth: "130px",
  appearance: "auto" as React.CSSProperties["appearance"],
};

export default function FilterBar({
  total,
  filterOptions,
}: {
  total: number;
  filterOptions?: FilterOptions;
}) {
  const router = useRouter();
  const params = useSearchParams();

  const push = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value) {
        next.set(key, value);
      } else {
        next.delete(key);
      }
      next.delete("offset");
      router.push(`/market?${next.toString()}`);
    },
    [params, router]
  );

  const category = params.get("category") ?? "";
  const source   = params.get("source")   ?? "";
  const sort     = params.get("sort")     ?? "price_asc";
  const minPrice = params.get("min_price") ?? "";
  const maxPrice = params.get("max_price") ?? "";

  return (
    <div
      className="sticky top-[52px] z-40 border-b"
      style={{
        background: "rgba(244,244,245,0.92)",
        borderColor: "var(--border)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
      }}
    >
      <div className="max-w-6xl mx-auto px-6 py-3 flex flex-wrap items-center gap-3">
        {/* Part count */}
        <span className="mono mr-1" style={{ color: "var(--text-dim)", fontSize: "0.7rem" }}>
          {total.toLocaleString()} parts
        </span>

        {/* Category */}
        <select
          value={category}
          onChange={e => push("category", e.target.value)}
          style={selectStyle}
        >
          <option value="">All Categories</option>
          {CATEGORIES.map(c => (
            <option key={c} value={c}>{c.toUpperCase()}</option>
          ))}
        </select>

        {/* Source */}
        <select
          value={source}
          onChange={e => push("source", e.target.value)}
          style={selectStyle}
        >
          <option value="">All Retailers</option>
          {SOURCES.map(s => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>

        {/* Dynamic spec filters — only shown when a category is selected */}
        {filterOptions &&
          Object.entries(filterOptions).map(([key, values]) =>
            values && values.length > 0 ? (
              <select
                key={key}
                value={params.get(key) ?? ""}
                onChange={e => push(key, e.target.value)}
                style={selectStyle}
              >
                <option value="">
                  All {SPEC_LABELS[key] ?? key.replace(/_/g, " ")}
                </option>
                {values.map(v => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
            ) : null
          )}

        {/* Price range */}
        <div className="flex items-center gap-1.5">
          <input
            type="number"
            placeholder="Min PKR"
            value={minPrice}
            onChange={e => push("min_price", e.target.value)}
            style={{ ...selectStyle, minWidth: "90px", width: "90px" }}
          />
          <span className="mono" style={{ color: "var(--text-dim)", fontSize: "0.7rem" }}>—</span>
          <input
            type="number"
            placeholder="Max PKR"
            value={maxPrice}
            onChange={e => push("max_price", e.target.value)}
            style={{ ...selectStyle, minWidth: "90px", width: "90px" }}
          />
        </div>

        {/* Sort — pushed to right */}
        <select
          value={sort}
          onChange={e => push("sort", e.target.value)}
          style={{ ...selectStyle, marginLeft: "auto" }}
        >
          <option value="price_asc">Price: Low → High</option>
          <option value="price_desc">Price: High → Low</option>
        </select>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Update `frontend/app/market/page.tsx` to fetch filter options and pass spec params**

Replace the entire file:

```tsx
import { Suspense } from "react";
import Navbar from "@/components/Navbar";
import FilterBar from "@/components/FilterBar";
import PartRow from "@/components/PartRow";
import { getParts, getFilterOptions, type FilterOptions } from "@/lib/api";

interface PageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function str(v: string | string[] | undefined): string | undefined {
  if (!v) return undefined;
  return Array.isArray(v) ? v[0] : v;
}

const SPEC_KEYS = [
  "brand", "socket", "vram", "ddr_type", "speed", "chipset",
  "wattage", "rating", "form_factor", "type", "aio_size",
  "fan_size", "interface", "capacity",
] as const;

async function PartsList({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>;
}) {
  const category = str(searchParams.category);
  const source   = str(searchParams.source);
  const sort     = (str(searchParams.sort) as "price_asc" | "price_desc") ?? "price_asc";
  const minPrice = str(searchParams.min_price);
  const maxPrice = str(searchParams.max_price);
  const offset   = str(searchParams.offset);

  const specParams: Record<string, string> = {};
  for (const key of SPEC_KEYS) {
    const val = str(searchParams[key]);
    if (val) specParams[key] = val;
  }

  const [{ items, total }, filterOptions] = await Promise.all([
    getParts({
      category,
      source,
      min_price: minPrice ? Number(minPrice) : undefined,
      max_price: maxPrice ? Number(maxPrice) : undefined,
      sort,
      limit: 50,
      offset: offset ? Number(offset) : 0,
      ...specParams,
    }),
    category ? getFilterOptions(category) : Promise.resolve<FilterOptions>({}),
  ]);

  const heading = category
    ? category.charAt(0).toUpperCase() + category.slice(1).toUpperCase() + "s"
    : "All Parts";

  return (
    <>
      <Suspense>
        <FilterBar total={total} filterOptions={category ? filterOptions : undefined} />
      </Suspense>

      <div className="max-w-6xl mx-auto px-6 py-6 w-full">
        {/* Section header */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <p className="section-label mb-1">Browse Parts</p>
            <h1
              className="font-bold"
              style={{
                fontSize: "clamp(1.2rem, 2.5vw, 1.6rem)",
                color: "var(--text)",
              }}
            >
              {heading}
            </h1>
          </div>
          {total > 0 && (
            <span
              className="mono hidden sm:block"
              style={{ color: "var(--text-dim)", fontSize: "0.7rem" }}
            >
              {total.toLocaleString()} results
            </span>
          )}
        </div>

        {/* Parts list card */}
        <div
          className="rounded-xl overflow-hidden"
          style={{ border: "1px solid var(--border)" }}
        >
          {/* Purple top accent stripe */}
          <div
            className="h-px w-full"
            style={{
              background: "linear-gradient(90deg, #7c3aed, transparent 60%)",
            }}
          />

          {items.length === 0 ? (
            <div
              className="py-20 text-center"
              style={{ background: "var(--bg-card)" }}
            >
              <p
                className="mono"
                style={{ fontSize: "0.8rem", color: "var(--text-dim)" }}
              >
                NO PARTS FOUND
              </p>
              <p
                className="text-sm mt-1"
                style={{ color: "var(--text-muted)" }}
              >
                Try adjusting your filters
              </p>
            </div>
          ) : (
            items.map((part) => <PartRow key={part.id} part={part} />)
          )}
        </div>

        {/* Pagination hint */}
        {total > 50 && (
          <p
            className="mono mt-4 text-center"
            style={{ color: "var(--text-dim)", fontSize: "0.7rem" }}
          >
            Showing 50 of {total.toLocaleString()} parts — pagination coming
            soon
          </p>
        )}
      </div>
    </>
  );
}

export default async function MarketPage({ searchParams }: PageProps) {
  const resolvedParams = await searchParams;

  return (
    <div className="flex flex-col flex-1" style={{ background: "var(--bg)" }}>
      <Navbar />
      <main className="flex flex-col flex-1">
        <Suspense
          fallback={
            <div
              className="py-20 text-center mono"
              style={{ color: "var(--text-dim)" }}
            >
              Loading…
            </div>
          }
        >
          <PartsList searchParams={resolvedParams} />
        </Suspense>
      </main>
      <footer
        className="border-t px-6 py-6 text-center text-xs"
        style={{
          background: "var(--bg)",
          borderColor: "var(--border)",
          color: "var(--text-dim)",
        }}
      >
        PakPC — prices updated regularly from Pakistani retailers
      </footer>
    </div>
  );
}
```

- [ ] **Step 3: Type-check frontend**

```bash
cd /home/azaan/Documents/PPC/frontend
npx tsc --noEmit 2>&1 | head -40
```

Expected: No errors in `FilterBar.tsx`, `market/page.tsx`, or `lib/api.ts`.

- [ ] **Step 4: Start backend + frontend and do end-to-end test**

Terminal 1:
```bash
cd /home/azaan/Documents/PPC/backend && uvicorn main:app --port 8000
```

Terminal 2:
```bash
cd /home/azaan/Documents/PPC/frontend && npm run dev
```

Navigate to `http://localhost:3000/market`:
1. No category selected → no spec dropdowns visible
2. Select "CPU" → Brand and Socket dropdowns appear with populated values
3. Select "AM5" from Socket → part list updates, only AM5 CPUs shown
4. Select "GPU" → Brand and VRAM dropdowns appear
5. Select "8GB" VRAM → part list filters to 8GB GPUs only

- [ ] **Step 5: Kill dev servers and commit**

```bash
git add frontend/components/FilterBar.tsx frontend/app/market/page.tsx
git commit -m "feat: add dynamic spec filter dropdowns to market page"
```
