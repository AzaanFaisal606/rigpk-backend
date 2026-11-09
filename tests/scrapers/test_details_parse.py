import json

import pytest

from scrapers.details.parse import (
    parse_details, raw_hash, shopify_raw, webx_raw, woo_raw,
)


@pytest.fixture
def P(load_details_fixture):
    def _p(fixture, kind, source):
        item = load_details_fixture(fixture)
        build = {"woo": woo_raw, "shopify": shopify_raw, "webx": webx_raw}[kind]
        name = item.get("name") or item.get("title") or item.get("productName") or ""
        return parse_details(build(item, source), name)
    return _p


def test_redtech_psu(P):
    d = P("redtech_psu.json", "woo", "redtech.pk")
    assert d["overview"].startswith("The Corsair CX750 750W PSU is an 80 PLUS Bronze")
    assert len(d["specs"]) >= 12 and d["highlights"] == []
    assert "Pakistan" not in (d["overview"] or "")


def test_techmatched_gpu_no_ads(P):
    d = P("techmatched_gpu.json", "woo", "techmatched.pk")
    blob = json.dumps(d).lower()
    assert "price-match" not in blob and "check availability" not in blob and "manufacturer" not in blob
    assert "16GB GDDR6 VRAM" in d["highlights"]
    assert any(s[1] == "Memory Bus" for s in d["specs"])


def test_rbt_ram_spec_list_becomes_specs(P):
    d = P("rbt_ram.json", "woo", "rbtechngames.com")
    assert [None, "Capacity", "64GB (2x32GB)"] in d["specs"] and d["highlights"] == []


def test_rbt_gpu_highlights_and_grouped_specs(P):
    d = P("rbt_gpu.json", "woo", "rbtechngames.com")
    assert len(d["highlights"]) == 4 and d["highlights"][0].startswith("High-Performance Architecture")
    assert ["GPU Specifications", "CUDA® Cores", "2176 Units"] in d["specs"]


def test_techarc_gpu(P):
    d = P("tech_gpu.json", "woo", "techarc.pk")
    assert len(d["highlights"]) == 8 and "1-year warranty" in d["highlights"]
    assert "Tech Arc" not in (d["overview"] or "") and d["overview"]


def test_amd_case_is_empty(P):
    assert P("amd_case.json", "woo", "amdhouse.pk") == {"overview": None, "highlights": [], "specs": []}


def test_amd_cpu_overview_without_store_line(P):
    d = P("amd_cpu.json", "woo", "amdhouse.pk")
    assert d["overview"].startswith("Featuring highest boost clocks") and "AMD House" not in d["overview"]


def test_amd_motherboard_manufacturer_table(P):
    d = P("amd_motherboard.json", "woo", "amdhouse.pk")
    assert [None, "Chipset", "AMD B850"] in d["specs"]


def test_pakbyte_case_cut_at_seo_heading(P):
    d = P("pakbyte_case.json", "shopify", "pakbyte.pk")
    assert d["overview"].startswith("Special Edition fallout ATX mid-Tower")
    assert [None, "Material", "SPCC,ABS"] in d["specs"]
    assert not any("Dynamic RGB lighting" == h for h in d["highlights"])


def test_pakbyte_cpu(P):
    d = P("pakbyte_cpu.json", "shopify", "pakbyte.pk")
    assert "6 Cores & 12 Threads" in d["highlights"] and d["overview"].startswith("The AMD Ryzen 5 5500X3D")


def test_czone_gpu_webx(P):
    d = P("czone_gpu.json", "webx", "czone.com.pk")
    assert len(d["highlights"]) == 4 and d["highlights"][1].startswith("4GB GDDR5")
    assert d["overview"].startswith("Boost game performance") and d["specs"] == []


def test_junaid_bullets_split_from_overview(P):
    d = P("junaid_gpu.json", "webx", "junaidtech.pk")
    assert "Silent passive cooling means true 0dB" in d["highlights"]
    assert "•" not in d["overview"]


def test_zah_page_synthetic():
    raw = {
        "kind": "zah_page", "source": "zahcomputers.pk",
        "highlights_html": "<ul><li>8GB GDDR6</li><li>Material: Steel</li><li>8gb gddr6</li></ul>",
        "desc_html": "<table><tr><td>Chipset</td><td>B850</td></tr></table>"
                     "<p>A solid board for modern builds with lots of connectivity options. Zah Computers sells this.</p>",
    }
    d = parse_details(raw, "Some Board")
    assert d["highlights"] == ["8GB GDDR6", "Material: Steel"]
    assert d["specs"] == [[None, "Chipset", "B850"]]
    assert d["overview"].startswith("A solid board")


def test_raw_hash_stable():
    a = {"kind": "woo", "source": "x", "description": "é"}
    assert raw_hash(a) == raw_hash(dict(reversed(list(a.items())))) and len(raw_hash(a)) == 64


def test_caps_and_unknown_kind():
    big = {"kind": "woo", "source": "redtech.pk", "short_description": "",
           "description": "<ul>" + "".join(f"<li>Feature number {i} is great</li>" for i in range(40)) + "</ul>"}
    assert len(parse_details(big)["highlights"]) == 12
    with pytest.raises(ValueError):
        parse_details({"kind": "nope", "source": "x"})


def test_techmatched_overview_skips_ad_paragraph(P):
    d = P("techmatched_gpu.json", "woo", "techmatched.pk")
    assert "match the price" not in (d["overview"] or "")


def test_spec_list_non_kv_items_become_highlights(P):
    d = P("pakbyte_case.json", "shopify", "pakbyte.pk")
    assert any(h.startswith("Convenient top-mounted I/O panel") for h in d["highlights"])
    d = P("pakbyte_gpu.json", "shopify", "pakbyte.pk")
    assert any("PhantomLink" in h for h in d["highlights"])


def test_webx_delivery_highlights_dropped():
    raw = {"kind": "webx", "source": "junaidtech.pk", "desc": "",
           "highlights": "80 PLUS Bronze Certified_Free Delivery All Over Pakistan_ATX 3.1 & PCIe 5.1 Compliant"}
    assert parse_details(raw)["highlights"] == ["80 PLUS Bronze Certified", "ATX 3.1 & PCIe 5.1 Compliant"]


def test_zah_category_template_gives_no_overview():
    desc = (
        "<p>Welcome to the Storage section at ZahComputers.pk! Here, you'll find a wide range of drives.</p>"
        "<p>Whether you need extra space to store large files, faster access for intensive applications, "
        "or a reliable backup, these drives cover it.</p>"
    )
    raw = {"kind": "zah_page", "source": "zahcomputers.pk", "highlights_html": "<ul><li>2TB</li></ul>", "desc_html": desc}
    d = parse_details(raw, "Seagate Barracuda 2TB")
    assert d["overview"] is None
    assert d["highlights"] == ["2TB"]


def test_zah_real_description_still_gives_overview():
    desc = "<p>The Cougar MX600 Air Pro is a premium high-airflow mid-tower gaming case designed for powerful builds.</p>"
    raw = {"kind": "zah_page", "source": "zahcomputers.pk", "highlights_html": "", "desc_html": desc}
    assert parse_details(raw, "Cougar MX600 Air Pro")["overview"].startswith("The Cougar MX600")
