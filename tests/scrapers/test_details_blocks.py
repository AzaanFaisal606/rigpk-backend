from scrapers.details.blocks import is_spec_list, list_blocks, paragraphs, split_kv, table_specs
from scrapers.details.html_tree import parse_html


def test_redtech_psu_table(load_details_fixture):
    it = load_details_fixture("redtech_psu.json")
    specs = table_specs(parse_html(it["description"]))
    assert [None, "Power Output", "750W"] in specs
    assert not any(s[1] in ("Specification", "Brand") and s[2] in ("Details",) for s in specs)
    assert len(specs) >= 12


def test_rbt_grouped_rows_split(load_details_fixture):
    specs = table_specs(parse_html(load_details_fixture("rbt_gpu.json")["description"]))
    assert ["GPU Specifications", "CUDA® Cores", "2176 Units"] in specs
    assert ["Memory Specs", "Memory Bus", "256-bit"] in specs


def test_techmatched_multiline_cell(load_details_fixture):
    specs = table_specs(parse_html(load_details_fixture("techmatched_gpu.json")["description"]))
    out = {s[1]: s[2] for s in specs}
    assert out["Memory Bus"] == "128-bit"
    assert "Note" not in out
    assert "Boost Clock" in out["Core Clock"] and "Game Clock" in out["Core Clock"]


def test_spec_list_vs_highlights(load_details_fixture):
    ram = list_blocks(parse_html(load_details_fixture("rbt_ram.json")["short_description"]))[0]
    assert is_spec_list(ram) and split_kv(ram[0]) == ("Capacity", "64GB (2x32GB)")
    gpu = list_blocks(parse_html(load_details_fixture("rbt_gpu.json")["short_description"]))[0]
    assert not is_spec_list(gpu)


def test_nested_list_folds(load_details_fixture):
    blocks = list_blocks(parse_html(load_details_fixture("techmatched_gpu.json")["description"]))
    assert any("WINDFORCE cooling system" in i and "Hawk fan" in i for b in blocks for i in b)


def test_paragraphs_skip_headings(load_details_fixture):
    ps = paragraphs(parse_html(load_details_fixture("redtech_psu.json")["description"]))
    assert ps[0].startswith("The Corsair CX750 750W PSU is a reliable")


def test_dl_pairs_and_dropped_labels():
    root = parse_html("<dl><dt>Socket:</dt><dd>AM5</dd></dl>"
                      "<table><tr><td>SKU</td><td>123</td></tr><tr><td>Cores</td><td></td></tr>"
                      "<tr><td>A</td><td>B</td><td>C</td></tr></table>")
    assert table_specs(root) == [[None, "Socket", "AM5"]]
