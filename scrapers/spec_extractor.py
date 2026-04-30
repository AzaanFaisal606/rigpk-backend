from __future__ import annotations
import re
from typing import Optional

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
    ("radeon",          "AMD"),
    ("ryzen",           "AMD"),
    ("geforce",         "NVIDIA"),
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
    ("wd",              "WD"),
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


_SOCKET_RE = re.compile(r'\b(AM[45]|LGA\s?\d{4})\b', re.IGNORECASE)


def _extract_socket(name: str) -> Optional[str]:
    m = _SOCKET_RE.search(name)
    if m:
        return m.group(1).replace(" ", "").upper()
    return None


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


_DDR_TYPE_RE = re.compile(r'\b(L?P?DDR[45]X?)\b', re.IGNORECASE)
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


_CHIPSET_RE = re.compile(r'\b([ABXHZ]\d{3}[EFMKPS]?)\b', re.IGNORECASE)
_VALID_CHIPSET_PREFIXES = (
    'A3', 'A4', 'A5', 'A6',
    'B3', 'B4', 'B5', 'B6', 'B7',
    'X3', 'X4', 'X5', 'X6', 'X8',
    'H3', 'H4', 'H5', 'H6', 'H7',
    'Z3', 'Z4', 'Z5', 'Z6', 'Z7', 'Z8',
)


def _extract_chipset(name: str) -> Optional[str]:
    for m in _CHIPSET_RE.finditer(name):
        cs = m.group(1).upper()
        if any(cs.startswith(p) for p in _VALID_CHIPSET_PREFIXES):
            return cs
    return None


_PSU_WATTS_RE = re.compile(r'\b(\d{3,4})W\b', re.IGNORECASE)
_PSU_RATING_RE = re.compile(
    r'80(?:\s+plus|\s*\+)\s*(Bronze|Silver|Gold|Platinum|Titanium|White)',
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


_COOLING_SIZE_RE = re.compile(
    r'\b(80|92|120|140|200|240|280|360|420)\s*mm\b', re.IGNORECASE
)


def _extract_cooling_type(name: str) -> Optional[str]:
    lower = name.lower()
    if re.search(r'\b(aio|liquid\s+cool|water\s+cool|hydro|all[\s-]in[\s-]one)\b', lower):
        return "AIO"
    if re.search(r'\b(air[\s-]+cooler|heatsink|cpu[\s-]+cooler|tower[\s-]+cooler?|air[\s-]+tower|dual[\s-]+tower)\b', lower):
        return "Air"
    if re.search(r'\b(case\s+fan|argb\s+fan|rgb\s+fan|thermal\s+paste|thermal\s+pad)\b', lower):
        return "Fan/Accessory"
    return None


def _extract_cooling_size(name: str) -> Optional[str]:
    m = _COOLING_SIZE_RE.search(name)
    return f"{m.group(1)}mm" if m else None


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

    elif category in ("hdd", "monitor"):
        pass  # brand-only; _extract_brand() above already handles it

    return specs
