from __future__ import annotations
import re
from typing import Optional

from scrapers.socket_table import socket_for

# Manufacturers — the entity that actually built/boxed the part (ASUS, MSI,
# Gigabyte, Sapphire...). Checked first: `brand` on the market page's filter
# is "who do I buy this from", not "whose chip is inside".
_MAKER_BRANDS: list[tuple[str, str]] = [
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
    ("zotac",           "Zotac"),
    ("palit",           "Palit"),
    ("arktek",          "Arktek"),
    ("adata",           "ADATA"),
    ("dahua",           "Dahua"),
    ("hikvision",       "Hikvision"),
    ("asus",            "ASUS"),
    ("nzxt",            "NZXT"),
    ("benq",            "BenQ"),
    ("dell",            "Dell"),
    ("lenovo",          "Lenovo"),
    ("philips",         "Philips"),
    ("iiyama",          "iiyama"),
    ("msi",             "MSI"),
    ("xpg",             "XPG"),
    ("xfx",             "XFX"),
    ("pny",             "PNY"),
    ("aoc",             "AOC"),
    ("wd",              "WD"),
    ("lg",              "LG"),
    # Budget/grey-market board partners, verified against the active
    # catalogue (were falling through to the chip-vendor fallback below).
    ("afox",            "AFOX"),
    ("ninja",           "Ninja"),
    ("ease",            "EASE"),
    ("colorful",        "Colorful"),
    ("maxsun",          "MAXSUN"),
    ("darkflash",       "DarkFlash"),
    ("leadtek",         "Leadtek"),
    ("gunnir",          "Gunnir"),
    ("galax",           "Galax"),
    ("manli",           "Manli"),
    ("inno3d",          "Inno3D"),
    ("evga",            "EVGA"),
    ("biostar",         "Biostar"),
    ("yeston",          "Yeston"),
    ("onda",            "Onda"),
    ("vastarmor",       "Vastarmor"),
    ("alseye",          "Alseye"),
    ("dataland",        "Dataland"),
    # Misspelling seen in real listings ("Saphire RX590 Nitro Plus...") —
    # canonicalizes to the existing Sapphire value, not a second brand.
    ("saphire",         "Sapphire"),
]

# Chip vendors — fallback only. For CPUs there is no third-party maker, so
# this list is where CPU brand actually resolves (AMD/Intel ARE the maker
# there). For GPUs it only fires when no board partner was named at all.
_CHIP_VENDOR_BRANDS: list[tuple[str, str]] = [
    ("radeon",          "AMD"),
    ("ryzen",           "AMD"),
    ("geforce",         "NVIDIA"),
    ("intel",           "Intel"),
    ("nvidia",          "NVIDIA"),
    ("amd",             "AMD"),
]

_ALL_BRANDS = _MAKER_BRANDS + _CHIP_VENDOR_BRANDS

# Tokens matched on a strict \b word boundary rather than a plain substring:
# short (<=3 char) codes, plus longer single words empirically shown to
# collide with unrelated substrings in real listings — "ease" fires inside
# "Q-Release"/"Quick Release" (ASUS motherboards), "galax" fires inside
# "Galaxy" (Xigmatek fan kits). Every maker added after the original list is
# boundary-matched by default: a bare substring check is unsafe for any
# short, ordinary-looking brand word.
_BOUNDARY_BRANDS = {
    "afox", "ninja", "ease", "colorful", "maxsun", "darkflash", "leadtek",
    "gunnir", "galax", "manli", "inno3d", "evga", "biostar", "yeston",
    "onda", "vastarmor", "alseye", "dataland", "saphire",
}
_SHORT_BRAND_RE: dict[str, re.Pattern] = {
    s: re.compile(rf'\b{re.escape(s)}\b', re.IGNORECASE)
    for s, _ in _ALL_BRANDS if len(s) <= 3 or s in _BOUNDARY_BRANDS
}


def _match_brand_list(lower: str, brands: list[tuple[str, str]]) -> Optional[str]:
    for match_str, canonical in brands:
        pattern = _SHORT_BRAND_RE.get(match_str)
        if pattern:
            if pattern.search(lower):
                return canonical
        elif match_str in lower:
            return canonical
    return None


def _extract_brand(name: str) -> Optional[str]:
    lower = name.lower()
    maker = _match_brand_list(lower, _MAKER_BRANDS)
    if maker:
        return maker
    return _match_brand_list(lower, _CHIP_VENDOR_BRANDS)


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


# ----------------------------------------------------------------------
# GPU / CPU model extraction — STRICT curated allowlist
# ----------------------------------------------------------------------
# Only models in these sets get a `model` spec. The regexes pull a candidate
# from the (messy) product name; the candidate is normalized to canonical form
# and kept only if it's on the allowlist. Unlisted/obscure models -> no model
# (excluded from trends). Lists are curated to the popular desktop parts that
# matter in the Pakistani market (incl. older used-market gens). Extend as new
# generations launch.

# --- GPU allowlist (canonical form: "<FAMILY> <NUM><suffix>") ---
_GPU_MODELS = frozenset({
    # NVIDIA RTX 50 (Blackwell)
    "RTX 5090", "RTX 5080", "RTX 5070 Ti", "RTX 5070", "RTX 5060 Ti", "RTX 5060",
    # NVIDIA RTX 40 (Ada)
    "RTX 4090", "RTX 4080 Super", "RTX 4080", "RTX 4070 Ti Super", "RTX 4070 Ti",
    "RTX 4070 Super", "RTX 4070", "RTX 4060 Ti", "RTX 4060",
    # NVIDIA RTX 30 (Ampere)
    "RTX 3090 Ti", "RTX 3090", "RTX 3080 Ti", "RTX 3080", "RTX 3070 Ti", "RTX 3070",
    "RTX 3060 Ti", "RTX 3060", "RTX 3050",
    # NVIDIA RTX 20 (Turing)
    "RTX 2080 Ti", "RTX 2080 Super", "RTX 2080", "RTX 2070 Super", "RTX 2070",
    "RTX 2060 Super", "RTX 2060",
    # NVIDIA GTX 16 (Turing, budget/used)
    "GTX 1660 Ti", "GTX 1660 Super", "GTX 1660", "GTX 1650 Super", "GTX 1650", "GTX 1630",
    # AMD RX 9000 (RDNA4)
    "RX 9070 XT", "RX 9070 GRE", "RX 9070", "RX 9060 XT", "RX 9060",
    # AMD RX 7000 (RDNA3)
    "RX 7900 XTX", "RX 7900 XT", "RX 7900 GRE", "RX 7800 XT", "RX 7700 XT",
    "RX 7600 XT", "RX 7600",
    # AMD RX 6000 (RDNA2)
    "RX 6950 XT", "RX 6900 XT", "RX 6800 XT", "RX 6800", "RX 6750 XT", "RX 6700 XT",
    "RX 6650 XT", "RX 6600 XT", "RX 6600", "RX 6500 XT", "RX 6400",
    # Intel Arc
    "Arc B580", "Arc B570", "Arc A770", "Arc A750", "Arc A580", "Arc A380",
})

# NVIDIA GeForce: RTX/GTX + 4-digit number + optional Ti/Super (incl. "Ti Super").
#   Handles glued forms ("3060Ti") and the trademark char ("RTX™ 4070").
_GPU_NVIDIA_RE = re.compile(
    r'\b(RTX|GTX)\s*™?\s*(\d{4})\s*(Ti\s*Super|Ti|Super)?',
    re.IGNORECASE,
)
# AMD Radeon: RX + 4-digit number + optional XTX/XT/GRE.
_GPU_AMD_RE = re.compile(r'\bRX\s*(\d{4})\s*(XTX|XT|GRE)?', re.IGNORECASE)
# Intel Arc: "Arc A770", "Arc B580".
_GPU_ARC_RE = re.compile(r'\bArc\s+([AB]\d{3})\b', re.IGNORECASE)


def _norm_gpu_suffix(suffix: Optional[str]) -> str:
    if not suffix:
        return ""
    s = re.sub(r'\s+', ' ', suffix.strip()).lower()
    return {
        "ti super": " Ti Super",
        "ti": " Ti",
        "super": " Super",
        "xt": " XT",
        "xtx": " XTX",
        "gre": " GRE",
    }.get(s, "")


def _extract_gpu_model(name: str) -> Optional[str]:
    """Return canonical GPU model if it's on the allowlist, else None."""
    candidates = []

    m = _GPU_AMD_RE.search(name)
    if m:
        candidates.append(f"RX {m.group(1)}{_norm_gpu_suffix(m.group(2))}")

    m = _GPU_ARC_RE.search(name)
    if m:
        candidates.append(f"Arc {m.group(1).upper()}")

    m = _GPU_NVIDIA_RE.search(name)
    if m:
        candidates.append(f"{m.group(1).upper()} {m.group(2)}{_norm_gpu_suffix(m.group(3))}")

    for c in candidates:
        if c in _GPU_MODELS:
            return c
    return None


# --- CPU allowlist (canonical: "i<t>-<num><sfx>", "Ryzen <t> <num><sfx>", "Ultra <t> <num><sfx>") ---
_CPU_MODELS = frozenset({
    # AMD Ryzen 9000 (Zen 5)
    "Ryzen 9 9950X3D", "Ryzen 9 9950X", "Ryzen 9 9900X3D", "Ryzen 9 9900X",
    "Ryzen 7 9850X3D", "Ryzen 7 9800X3D", "Ryzen 7 9700X", "Ryzen 5 9600X", "Ryzen 5 9600",
    # AMD Ryzen 7000 (Zen 4)
    "Ryzen 9 7950X3D", "Ryzen 9 7950X", "Ryzen 9 7900X3D", "Ryzen 9 7900X", "Ryzen 9 7900",
    "Ryzen 7 7800X3D", "Ryzen 7 7700X", "Ryzen 7 7700", "Ryzen 5 7600X", "Ryzen 5 7600",
    "Ryzen 5 7500F",
    # AMD Ryzen 5000 (Zen 3)
    "Ryzen 9 5950X", "Ryzen 9 5900X", "Ryzen 7 5800X3D", "Ryzen 7 5800X", "Ryzen 7 5700X3D",
    "Ryzen 7 5700X", "Ryzen 7 5700G", "Ryzen 5 5600X", "Ryzen 5 5600G", "Ryzen 5 5600",
    "Ryzen 5 5500",
    # AMD Ryzen 3000 (Zen 2)
    "Ryzen 9 3900X", "Ryzen 7 3700X", "Ryzen 5 3600X", "Ryzen 5 3600", "Ryzen 5 3400G",
    "Ryzen 3 3300X", "Ryzen 3 3100",
    # Intel Core Ultra 200S (Arrow Lake)
    "Ultra 9 285K", "Ultra 7 265K", "Ultra 7 265KF", "Ultra 5 245K", "Ultra 5 245KF",
    # Intel 14th gen
    "i9-14900K", "i9-14900KF", "i9-14900F", "i7-14700K", "i7-14700KF", "i7-14700F",
    "i5-14600K", "i5-14600KF", "i5-14400F", "i3-14100F",
    # Intel 13th gen
    "i9-13900K", "i9-13900KF", "i9-13900F", "i7-13700K", "i7-13700KF", "i7-13700F",
    "i5-13600K", "i5-13600KF", "i5-13400F", "i3-13100F",
    # Intel 12th gen
    "i9-12900K", "i9-12900KF", "i7-12700K", "i7-12700KF", "i7-12700F", "i5-12600K",
    "i5-12600KF", "i5-12400F", "i3-12100F",
})

# Intel Core (legacy naming): i3/i5/i7/i9 + 4-5 digit number + optional letter suffix.
#   Handles "i5-12400F", "i5 14600K", "Core i7-14700K".
_CPU_INTEL_RE = re.compile(r'\bi([3579])[\s-]?(\d{4,5})([A-Z]{1,2})?\b', re.IGNORECASE)
# Intel Core Ultra 200S: "Core Ultra 9 285K", "Ultra 5 245KF".
_CPU_ULTRA_RE = re.compile(r'\bUltra\s+([579])\s+(\d{3})(KF|K)?\b', re.IGNORECASE)
# AMD Ryzen: "Ryzen 5 5600X", "Ryzen 7 7800X3D", "Ryzen 9 9950X3D".
_CPU_AMD_RE = re.compile(r'\bRyzen\s+([3579])\s+(\d{3,4})([A-Z0-9]{0,3})?\b', re.IGNORECASE)


def _extract_cpu_model(name: str) -> Optional[str]:
    """Return canonical CPU model if it's on the allowlist, else None."""
    candidates = []

    m = _CPU_ULTRA_RE.search(name)
    if m:
        candidates.append(f"Ultra {m.group(1)} {m.group(2)}{(m.group(3) or '').upper()}")

    m = _CPU_INTEL_RE.search(name)
    if m:
        candidates.append(f"i{m.group(1)}-{m.group(2)}{(m.group(3) or '').upper()}")

    m = _CPU_AMD_RE.search(name)
    if m:
        candidates.append(f"Ryzen {m.group(1)} {m.group(2)}{(m.group(3) or '').upper()}")

    for c in candidates:
        if c in _CPU_MODELS:
            return c
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


# Kit notation: "2x8GB", "2 x 16GB", "16GBx2" -> total = count * per-stick.
_RAM_KIT_RE = re.compile(r'(\d+)\s*x\s*(\d+)\s*GB', re.IGNORECASE)
_RAM_KIT_REV_RE = re.compile(r'(\d+)\s*GB\s*x\s*(\d+)', re.IGNORECASE)
# Plain total: first "<N>GB" token (after kit notation is handled).
_RAM_CAP_RE = re.compile(r'\b(\d+)\s*GB\b', re.IGNORECASE)
_VALID_RAM_CAP = {16, 32}


def _extract_ram_capacity(name: str) -> Optional[str]:
    """
    Total kit capacity, restricted to the standard sizes we track (16GB, 32GB).
    Prefers explicit kit math ("2x8GB" -> 16GB) over a bare "<N>GB" token.
    Returns None for sizes we don't track (8GB, 64GB+) or unparseable names.
    """
    total = None
    m = _RAM_KIT_RE.search(name)          # "2x8GB"
    if m:
        total = int(m.group(1)) * int(m.group(2))
    if total is None:
        m = _RAM_KIT_REV_RE.search(name)  # "16GBx2"
        if m:
            total = int(m.group(1)) * int(m.group(2))
    if total is None:
        m = _RAM_CAP_RE.search(name)      # bare "16GB"
        if m:
            total = int(m.group(1))
    if total in _VALID_RAM_CAP:
        return f"{total}GB"
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
        s = _extract_socket(name) or socket_for(name)
        if s:
            specs["socket"] = s
        m = _extract_cpu_model(name)
        if m:
            specs["model"] = m

    elif category == "gpu":
        v = _extract_vram(name)
        if v:
            specs["vram"] = v
        m = _extract_gpu_model(name)
        if m:
            specs["model"] = m

    elif category == "ram":
        d = _extract_ddr_type(name)
        if d:
            specs["ddr_type"] = d
        sp = _extract_ram_speed(name)
        if sp:
            specs["speed"] = sp
        cap = _extract_ram_capacity(name)
        if cap:
            specs["capacity"] = cap

    elif category == "motherboard":
        s = _extract_socket(name)
        if s:
            specs["socket"] = s
        cs = _extract_chipset(name)
        if cs:
            specs["chipset"] = cs
        ff = _extract_form_factor(name)
        if ff:
            specs["form_factor"] = ff

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
