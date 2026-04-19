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
