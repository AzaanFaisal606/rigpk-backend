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

def test_brand_hp_monitor():
    assert extract_specs("HP M27f - 75Hz 1080p FHD IPS 27\" Monitor", "monitor")["brand"] == "HP"


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
    # Genuinely undecidable: no model number at all. Unlike an unmatched-but-
    # real model (merely uncovered), there is no way to derive a socket from
    # this string even in principle — "i5" alone spans LGA1156 (i5-750)
    # through LGA1700 (i5-14600K), 8+ generations apart.
    assert "socket" not in extract_specs("Intel Core i5 Processor (Tray)", "cpu")


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

def test_ram_capacity_plain_16():
    assert extract_specs("Apacer 16GB DDR4 3200MHz", "ram")["capacity"] == "16GB"

def test_ram_capacity_kit_2x8():
    # 2x8GB kit totals 16GB
    assert extract_specs("Corsair Vengeance LPX 16GB 2x8GB DDR4 3200MHz", "ram")["capacity"] == "16GB"

def test_ram_capacity_kit_2x16():
    assert extract_specs("XPG Spectrix 32GB 2x16GB DDR4 3600MHz", "ram")["capacity"] == "32GB"

def test_ram_capacity_kit_reversed():
    # "16GBx2" notation totals 32GB
    assert extract_specs("G.Skill 16GBx2 DDR5 6000MHz", "ram")["capacity"] == "32GB"

def test_ram_capacity_8gb_excluded():
    # 8GB not a tracked capacity
    assert "capacity" not in extract_specs("Lexar 8GB DDR4-3200 UDIMM", "ram")

def test_ram_capacity_64gb_excluded():
    assert "capacity" not in extract_specs("Kingston Fury 64GB 2x32GB DDR5 6000", "ram")


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


# ── GPU model (strict allowlist) ─────────────────────────────────────────────

def test_gpu_model_rtx_basic():
    assert extract_specs("MSI GeForce RTX 5070 Gaming OC 12GB", "gpu")["model"] == "RTX 5070"

def test_gpu_model_ti_super():
    assert extract_specs("MSI RTX 4070 Ti Super 16GB", "gpu")["model"] == "RTX 4070 Ti Super"

def test_gpu_model_glued_ti():
    # "3060Ti" with no space must still resolve to "RTX 3060 Ti"
    assert extract_specs("Gainward RTX 3060Ti Dual Fan", "gpu")["model"] == "RTX 3060 Ti"

def test_gpu_model_trademark_char():
    assert extract_specs("ASUS Dual GeForce RTX™ 4070 12GB", "gpu")["model"] == "RTX 4070"

def test_gpu_model_amd_xt():
    assert extract_specs("Sapphire Pulse RX 7800 XT 16GB", "gpu")["model"] == "RX 7800 XT"

def test_gpu_model_amd_rdna4():
    assert extract_specs("Sapphire PURE Radeon RX 9070 XT", "gpu")["model"] == "RX 9070 XT"

def test_gpu_model_intel_arc():
    assert extract_specs("Intel Arc B580 12GB", "gpu")["model"] == "Arc B580"

def test_gpu_model_rtx20():
    assert extract_specs("EVGA GeForce RTX 2080 Super", "gpu")["model"] == "RTX 2080 Super"

def test_gpu_model_not_on_allowlist_rdna1():
    # RX 5600 XT (RDNA1) deliberately excluded
    assert "model" not in extract_specs("AMD YESTON Radeon RX 5600 XT 6GB", "gpu")

def test_gpu_model_not_on_allowlist_workstation():
    assert "model" not in extract_specs("LEADTEK Quadro RTX A5000 24GB", "gpu")

def test_gpu_model_not_on_allowlist_entry():
    assert "model" not in extract_specs("MSI GeForce GT 730 4GB", "gpu")


# ── CPU model (strict allowlist) ─────────────────────────────────────────────

def test_cpu_model_intel_dash():
    assert extract_specs("Intel Core i5-12400F Desktop", "cpu")["model"] == "i5-12400F"

def test_cpu_model_intel_space():
    # "i5 14600K" (space, not dash) must normalize to "i5-14600K"
    assert extract_specs("Intel i5 14600K Tray", "cpu")["model"] == "i5-14600K"

def test_cpu_model_amd_x3d():
    assert extract_specs("AMD Ryzen 7 7800X3D 8-Core", "cpu")["model"] == "Ryzen 7 7800X3D"

def test_cpu_model_amd_plain():
    assert extract_specs("AMD Ryzen 5 5600 Desktop", "cpu")["model"] == "Ryzen 5 5600"

def test_cpu_model_intel_ultra():
    assert extract_specs("Intel Core Ultra 9 285K", "cpu")["model"] == "Ultra 9 285K"

def test_cpu_model_intel_ultra_kf():
    assert extract_specs("Intel Core Ultra 5 245KF Processor", "cpu")["model"] == "Ultra 5 245KF"

def test_cpu_model_not_on_allowlist_apu():
    # Ryzen 5 8500G not on the curated list
    assert "model" not in extract_specs("AMD Ryzen 5 8500G Desktop", "cpu")

def test_cpu_model_not_on_allowlist_nonf():
    # plain i3-12100 (non-F) excluded; only the -F variant is listed
    assert "model" not in extract_specs("Intel Core i3-12100 Tray", "cpu")

def test_cpu_socket_still_extracted_with_model():
    r = extract_specs("AMD Ryzen 5 5600X AM4 Processor", "cpu")
    assert r["model"] == "Ryzen 5 5600X"
    assert r["socket"] == "AM4"


# ── Brand is the maker, not the chip vendor ──────────────────────────────────
#
# Ordering in the vendor list made NVIDIA/AMD/Intel win over the actual maker
# on 57.7% of GPUs — so the brand filter, one of the market page's primary
# filters, was wrong for the majority of the category people most want it for.

@pytest.mark.parametrize("name,expected", [
    ("Asus Dual GeForce RTX 4060 8GB GDDR6", "ASUS"),
    ("MSI GeForce RTX 4070 Ti Ventus 3X", "MSI"),
    ("Gigabyte AORUS Radeon RX 7900 XTX", "Gigabyte"),
    ("Sapphire Pulse Radeon RX 550 4GB", "Sapphire"),
    ("Zotac Gaming GeForce RTX 3060", "Zotac"),
])
def test_gpu_brand_is_the_maker(name, expected):
    assert extract_specs(name, "gpu")["brand"] == expected


# ── Budget/grey-market board partners missing from the original list ────────
#
# Found by querying the active catalogue for GPUs still resolving to the
# chip-vendor fallback (NVIDIA/AMD/Intel) or to no brand at all. Names below
# are taken verbatim from data/ppc.db.

@pytest.mark.parametrize("name,expected", [
    ("AFOX GT730 4GB 128bit DDR3 Low Profile PCI-E Gen 2.0 Graphics Card", "AFOX"),
    ("Ninja Nvidia GeForce GT 730 4GB Graphics Card", "Ninja"),
    ("Ease GeForce GT740 4GB Graphics Card", "EASE"),
    ("Colorful GT 1030 2GB V5-V GDDR5 Graphics Card", "Colorful"),
    ("Maxsun Intel Arc B580 iCraft 12G Graphics Card", "MAXSUN"),
    ("Darkflash 1660S AIGO GTX 4GB-192 BIT DDR6 BIT DDR6 Graphic Card", "DarkFlash"),
    ("Leadtek Quadro RTX A2000 6GB Graphics Card", "Leadtek"),
    ("Gunnir INDEX ARC B580 12G New in 10 Months Warranty", "Gunnir"),
    ("Galax GeForce RTX 4060 EX 1-Click OC 8GB Graphics Card", "Galax"),
    ("Manli RTX™ 4070 Ti Gallardo 12GB Graphics Card", "Manli"),
    ("Inno3D GeForce RTX 4060 Twin X2 8GB GDDR6 Gaming Graphics Card", "Inno3D"),
    ("EVGA GeForce RTX 3080 FTW3 Ultra Gaming Graphics Card", "EVGA"),
    ("Biostar AMD Radeon RX 7900 XT 20GB Graphics Card - Free Delivery", "Biostar"),
    ("Amd Yeston Game Ace RX 9060 XT 16GB Tri-Fan New in 10 Months Warranty", "Yeston"),
    ("Amd ONDA Aegis Radeon RX 7600 XT 16GB GDDR6 New in 10 Months Warranty (White)", "Onda"),
    ("Amd VASTARMOR RX 9070 GRE 12GB White Alloy New in 10 Months Warranty", "Vastarmor"),
    ("ALSEYE AMD RX580 8 GB Graphics Card", "Alseye"),
    ("AMD Dataland RX 5600 XT 6GB X-Serial Ares Tri-Fan used without Box in 1 Month Warranty", "Dataland"),
])
def test_gpu_brand_budget_makers(name, expected):
    assert extract_specs(name, "gpu")["brand"] == expected


def test_brand_saphire_misspelling_normalizes_to_sapphire():
    """'Saphire' (real listing typo for Sapphire) must not become a second,
    distinct brand value — the filter would then show both spellings."""
    name = "Saphire RX590 Nitro Plus Special Blue Edition 8GB Graphic Card – Used"
    assert extract_specs(name, "gpu")["brand"] == "Sapphire"


# ── New maker tokens must not fire inside an unrelated word ─────────────────

def test_galax_does_not_match_inside_galaxy():
    """Xigmatek's 'Galaxy III' fan kit must not be mis-branded as Galax."""
    name = "Xigmatek Galaxy III Essential Arctic ARGB 3 Fan Pack"
    assert extract_specs(name, "cooling").get("brand") != "Galax"


def test_ease_does_not_match_inside_release():
    """'ease' as a bare substring lives inside 'Q-Release'/'Quick Release' —
    real ASUS motherboard names — so it must stay a whole-word match only."""
    name = (
        "ASUS ROG Strix X870E-E Gaming WiFi AM5 ATX Motherboard, 18+2+2 "
        "Power Stages, Dynamic OC Switcher, Core Flex, DDR5 AEMP, WiFi 7, "
        "5x M.2, PCIe 5.0, Q-Release Slim, USB4, AI OCing & Networking"
    )
    assert extract_specs(name, "motherboard")["brand"] == "ASUS"


def test_ease_mid_title_does_not_win_over_real_brand():
    """'Eye Ease' is a whole word ('ease' bounded on both sides) so the plain
    \\b match still fires — but it sits ~150 chars into the title, nowhere
    near where a retailer puts the maker. Real catalogue row (data/ppc.db
    id 25383/27077): must resolve to HP, not EASE."""
    name = (
        "HP Series 5 524sw 24 inch FHD Monitor, 100Hz, Full HD Display "
        "(1920 x 1080), IPS Panel, 99% sRGB, 1500:1 Contrast Ratio, 300 "
        "nits, Eye Ease with Eyesafe Certification"
    )
    assert extract_specs(name, "monitor")["brand"] == "HP"


def test_ease_at_start_of_title_still_resolves():
    """Real catalogue row (data/ppc.db): 'Ease' the board maker sits at
    position 0, well inside the brand-position window, so it must still
    resolve — the position restriction must not have broken the original
    case it was added for."""
    name = "Ease EM510B DDR4 Intel 10/11th Gen microATX Motherboard"
    assert extract_specs(name, "motherboard")["brand"] == "EASE"


@pytest.mark.parametrize("name,expected", [
    ("AMD Ryzen 7 9800X3D", "AMD"),
    ("Intel Core i5-13400F", "Intel"),
])
def test_cpu_brand_stays_the_chip_vendor(name, expected):
    """For CPUs the chip vendor IS the manufacturer — nothing changes here."""
    assert extract_specs(name, "cpu")["brand"] == expected


# ── CPU socket derived from model (lookup table) ─────────────────────────────

@pytest.mark.parametrize("name,socket", [
    ("AMD Ryzen 7 9800X3D", "AM5"),
    ("AMD Ryzen 5 5600X", "AM4"),
    ("Intel Core i5-13400F", "LGA1700"),
    ("Intel Core i5-14600K", "LGA1700"),
    ("Intel Core Ultra 7 265K", "LGA1851"),
])
def test_cpu_socket_is_derived_from_the_model(name, socket):
    """
    Most CPU listings never state a socket. Deriving it from the model is what
    takes fill rate from 10.8% to something the compatibility checker can use.
    """
    assert extract_specs(name, "cpu")["socket"] == socket


def test_unknown_model_yields_no_socket_rather_than_a_wrong_one():
    specs = extract_specs("Some Unreleased CPU 9999", "cpu")
    assert "socket" not in specs or specs["socket"] is None


# ── CPU socket: mobile/soldered parts must NOT get a desktop socket ──────────
# Regression coverage for F1: Ryzen desktop/mobile and 10th/11th-gen Intel
# desktop/mobile share the same digit run within a generation. Only the
# suffix (or an explicit mobile marker) distinguishes them.

@pytest.mark.parametrize("name", [
    "AMD Ryzen 9 5900HX Mobile Processor",   # AMD mobile: HX suffix
    "AMD Ryzen 7 7840HS Processor",          # AMD mobile: HS suffix
    "AMD Ryzen 5 5600U Laptop Processor",    # AMD mobile: U suffix
    "Intel Core i7-10750H Laptop Processor", # 10th-gen mobile: same 5-digit run as desktop 10750
    "Intel Core Ultra 7 155H Processor",     # Meteor Lake mobile, no desktop digit match anyway
    "Intel Core Ultra 7 255H Mobile Processor",  # Arrow Lake-H: same 200-number space as desktop, H suffix
    "Intel Core i9-13980HX Laptop Processor",     # Intel mobile HX, explicit marker
])
def test_mobile_suffix_yields_no_socket(name):
    assert "socket" not in extract_specs(name, "cpu")


def test_explicit_mobile_marker_yields_no_socket_even_with_desktop_looking_digits():
    # "5600X" alone would resolve to AM4 — the "BGA" marker must veto it.
    specs = extract_specs("AMD Ryzen 5 5600X BGA Soldered Chip", "cpu")
    assert "socket" not in specs


@pytest.mark.parametrize("name,socket", [
    ("AMD Ryzen 9 5900X Desktop Processor", "AM4"),        # desktop counterpart of 5900HX
    ("AMD Ryzen 7 7840X Desktop Processor", "AM5"),        # desktop counterpart of 7840HS
    ("Intel Core Ultra 7 265K Processor", "LGA1851"),       # desktop counterpart of 255H
    ("AMD Ryzen 5 5600GE Desktop Processor", "AM4"),        # GE before G in suffix alternation
])
def test_desktop_suffix_still_resolves_after_mobile_exclusion(name, socket):
    assert extract_specs(name, "cpu")["socket"] == socket


# ── CPU socket: Pentium Gold G6xxx (F2) ───────────────────────────────────────

def test_pentium_gold_g6400_is_lga1200():
    # Previously the fixture for "no socket" — it's actually unambiguous.
    assert extract_specs("Intel Pentium Gold G6400 Desktop Processor", "cpu")["socket"] == "LGA1200"


# ── CPU socket: AMD "F" (no-iGPU) suffix (F fix round 2) ─────────────────────
# AMD's F suffix (e.g. 7500F, 9500F) is a real desktop-only Zen4 SKU, the
# same concept as Intel's F — it must resolve, not fall victim to the
# mobile-suffix tightening from round 1.

@pytest.mark.parametrize("name,socket", [
    ("AMD Ryzen 5 7500F", "AM5"),
    ("AMD Ryzen 5 9500F", "AM5"),
])
def test_amd_f_suffix_resolves_desktop_socket(name, socket):
    assert extract_specs(name, "cpu")["socket"] == socket


# ── CPU socket: Core Ultra tier↔model adjacency (F fix round 2) ──────────────
# Some real listings put marketing copy ("Desktop Processor") between the
# tier digit and the model number: "Core Ultra 5 Desktop Processor 245K".
# The adjacency requirement is relaxed to allow up to 2 intervening words —
# proven safe by scanning every row of the local DB across every category:
# the "core ultra [579]" trigger only ever appears on cpu rows, never on a
# motherboard/cooler listing that merely mentions Core Ultra compatibility.

@pytest.mark.parametrize("name,socket", [
    ("Intel Core Ultra 5 Desktop Processor 245K, 14 Cores 14 Threads", "LGA1851"),
    ("Intel Core Ultra 9 Desktop Processor 285K in Pakistan", "LGA1851"),
])
def test_core_ultra_tier_model_adjacency_allows_intervening_words(name, socket):
    assert extract_specs(name, "cpu")["socket"] == socket


def test_pentium_gold_alder_lake_g6405_not_matched():
    # Intel reused the "G6" prefix for 12th-gen Alder Lake (LGA1700, not
    # LGA1200) via G6405/G6405T. The rule is scoped to the exact Comet Lake
    # model numbers (G6400/G6500/G6600) so this must NOT resolve to LGA1200.
    assert "socket" not in extract_specs("Intel Pentium Gold G6405 Desktop Processor", "cpu")


# ── CPU socket/brand/model: real messy names from the local catalogue ────────
# Fixture data is otherwise idealized (clean "Brand Model Suffix" strings).
# These are verbatim names pulled from local data/ppc.db.

def test_real_name_html_entity():
    name = "AMD Ryzen 7 5700X Desktop Processor &#8211; Tray"
    specs = extract_specs(name, "cpu")
    assert specs["brand"] == "AMD"
    assert specs["socket"] == "AM4"
    assert specs["model"] == "Ryzen 7 5700X"


def test_real_name_tray_in_pakistan_suffix():
    name = "AMD Ryzen 5 5600X Tray Processor in Pakistan"
    specs = extract_specs(name, "cpu")
    assert specs["brand"] == "AMD"
    assert specs["socket"] == "AM4"
    assert specs["model"] == "Ryzen 5 5600X"


def test_real_name_long_spec_dump_title():
    name = (
        "Intel Core Ultra 5 245K – Core Ultra 5 (Series 2) Arrow Lake "
        "14-Core (6P+8E), LGA 1851, 125W Desktop Processor"
    )
    specs = extract_specs(name, "cpu")
    assert specs["brand"] == "Intel"
    assert specs["socket"] == "LGA1851"
    assert specs["model"] == "Ultra 5 245K"


def test_real_name_warranty_junk_suffix():
    name = "AMD Ryzen 5 3500X Chip New in 10 Months Warranty"
    specs = extract_specs(name, "cpu")
    assert specs["brand"] == "AMD"
    assert specs["socket"] == "AM4"


# ── Motherboard form_factor ───────────────────────────────────────────────────

@pytest.mark.parametrize("name,ff", [
    ("MSI B650 GAMING PLUS WIFI ATX Motherboard", "ATX"),
    ("Asus ROG Strix B550-I Gaming Mini-ITX", "Mini-ITX"),
    ("Gigabyte B760M DS3H Micro-ATX", "Micro-ATX"),
])
def test_motherboard_form_factor(name, ff):
    assert extract_specs(name, "motherboard")["form_factor"] == ff
