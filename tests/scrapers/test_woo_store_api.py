"""
WooStoreScraper: WooCommerce Store API items -> product rows, and the paging
loop's failure contract.

Fixtures in tests/fixtures/json/woo_<site>_products.json are whole items
picked from live /wp-json/wc/store/v1/products responses (2026-09-23), each
one unedited.
"""
import copy
import html as htmllib
import json
import urllib.parse

import pytest

from scrapers.base_scraper import HostBlocked
from scrapers.exceptions import ScrapeIncomplete
from scrapers.woo.base import WooStoreScraper
from scrapers.woo.stores import AmdHouseScraper, RbtScraper, TechMatchedScraper, clean_amd_name
from scrapers.zah.scraper import ZahScraper


def parse(scraper, items):
    return {p["url"]: p for p in (scraper.parse_item(i) for i in items) if p}


def by_id(items):
    return {i["id"]: i for i in items}


# ---- item -> row ------------------------------------------------------------

def test_zah_in_stock_item_becomes_a_row(load_json):
    item = by_id(load_json("woo_zah_products.json"))[248153]
    row = ZahScraper().parse_item(item)
    assert row["url"] == item["permalink"]
    assert row["price_pkr"] == 99000                 # "9900000" at minor unit 2
    assert row["source"] == "zahcomputers.pk"
    assert row["category"] == ""                     # stamped by run_all
    assert row["thumbnail_url"] == item["images"][0]["thumbnail"]


def test_out_of_stock_is_skipped(load_json):
    items = by_id(load_json("woo_zah_products.json"))
    assert items[247814]["is_in_stock"] is False
    assert ZahScraper().parse_item(items[247814]) is None


def test_price_zero_is_call_for_price_and_skipped(load_json):
    """zah lists "call for price" items in stock at 0, including one whose
    regular_price is set. The HTML scrapers emitted them with price None."""
    items = by_id(load_json("woo_zah_products.json"))
    for pid in (248560, 247722):
        assert items[pid]["is_in_stock"] is True
        assert ZahScraper().parse_item(items[pid]) is None


def test_names_are_html_decoded_exactly_once(load_json):
    items = load_json("woo_zah_products.json") + load_json("woo_amd_products.json")
    rows = list(parse(ZahScraper(), items).values())
    assert any("″" in r["name"] for r in rows), "fixture should carry a decoded &#8243;"
    for r in rows:
        assert "&#" not in r["name"] and "&amp;" not in r["name"], r["name"]
        assert htmllib.unescape(r["name"]) == r["name"], f"double-decoding changed {r['name']!r}"


def test_sale_price_is_the_price_paid(load_json):
    item = by_id(load_json("woo_amd_products.json"))[14277]
    assert item["prices"]["regular_price"] == "290000"
    assert AmdHouseScraper().parse_item(item)["price_pkr"] == 272000


def test_amd_warranty_text_is_stripped(load_json):
    """The shop page hides the warranty the API name carries. "used" and the
    box state stay: the condition spec reads them."""
    rows = {i["id"]: AmdHouseScraper().parse_item(i) for i in load_json("woo_amd_products.json")}
    assert rows[15266]["name"] == "PNY GeForce RTX 5070 Epic-X ARGB OC Triple Fan 12GB GDDR7 Graphics Card"
    assert rows[13372]["name"] == "Sapphire PULSE RX 570 4GB GDDR5 Used No Box"
    assert rows[14590]["name"] == "ASUS DUAL RTX 2070 SUPER EVO 8GB GDDR6 used"
    assert rows[14693]["name"] == "128GB NVME SSD used 2000mb/sec mixed brands"
    assert rows[15198]["name"] == "2TB Lexar® NM790 M.2 2280 PCIe Gen 4×4 NVMe SSD (no box)"
    assert rows[14343]["name"] == "Tracer X2 CPU Cooler 2 Heatpipe New"   # "03. " list number
    assert rows[15251] is None                                            # out of stock


def test_clean_amd_name_cases():
    assert clean_amd_name("AMD Ryzen 5 7600X Chip New in 10 Months Warranty") == "AMD Ryzen 5 7600X Chip"
    assert clean_amd_name("Amd ONDA RX 580 8GB GDDR6 New in 10 Months Warranty (White)") == \
        "Amd ONDA RX 580 8GB GDDR6 (White)"
    assert clean_amd_name("XFX RX 580 used 1 month warranty with box") == "XFX RX 580 used with box"
    assert clean_amd_name("AMD Ryzen 5 2600 chip only used 1month wty") == "AMD Ryzen 5 2600 chip only used"


def test_techmatched_seo_title_is_stripped(load_json):
    items = load_json("woo_techmatched_products.json")
    rows = parse(TechMatchedScraper(), items)
    names = {r["name"] for r in rows.values()}
    assert "Asus Rog Swift OLED PG27UCDM Gaming Monitor" in names
    assert "Valve Steam Controller" in names                     # " in Pakistan" suffix alone
    for n in names:
        assert not n.startswith("Buy ") and "TechMatched" not in n and not n.endswith("Pakistan")


def test_techmatched_stores_the_full_size_image(load_json):
    item = load_json("woo_techmatched_products.json")[0]
    assert TechMatchedScraper().parse_item(item)["thumbnail_url"] == item["images"][0]["src"]


def test_variable_product_uses_its_lowest_price(load_json):
    """For a variable product `prices.price` is the cheapest variation."""
    tm = by_id(load_json("woo_techmatched_products.json"))[33411]
    assert tm["type"] == "variable" and tm["prices"]["price_range"]["max_amount"] == "249900"
    assert TechMatchedScraper().parse_item(tm)["price_pkr"] == 1599

    rbt = load_json("woo_rbt_products.json")[0]           # currency_minor_unit 0
    assert rbt["type"] == "variable" and rbt["prices"]["currency_minor_unit"] == 0
    assert RbtScraper().parse_item(rbt)["price_pkr"] == 12999


# ---- paging and the failure contract ----------------------------------------

class FakeStore(WooStoreScraper):
    SOURCE = "store.test"
    BASE = "https://store.test"
    CATEGORIES = [("gpus", "gpu")]

    def __init__(self, pages):
        # pages: {page: list of items | Exception | raw str}
        self.pages = pages
        self.requested: list[str] = []

    def fetch(self, url, *a, **k):
        self.requested.append(url)
        page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["page"][0])
        content = self.pages.get(page, [])
        if isinstance(content, Exception):
            raise content
        return content if isinstance(content, str) else json.dumps(content)


@pytest.fixture
def make_items(load_json):
    base = by_id(load_json("woo_zah_products.json"))[248153]

    def _make(start, n):
        out = []
        for i in range(start, start + n):
            item = copy.deepcopy(base)
            item["permalink"] = f"https://store.test/product/p{i}/"
            out.append(item)
        return out
    return _make


def test_paging_stops_on_a_short_page(make_items):
    store = FakeStore({1: make_items(0, 100), 2: make_items(100, 3)})
    rows = store.scrape("gpus")
    assert len(rows) == 103
    assert len(store.requested) == 2, "a page under per_page is the last one"


def test_query_uses_the_slug_and_never_stock_status(make_items):
    store = FakeStore({1: make_items(0, 1)})
    store.scrape("gpus")
    url = store.requested[0]
    assert url.startswith("https://store.test/wp-json/wc/store/v1/products?")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query == {"category": ["gpus"], "per_page": ["100"], "page": ["1"]}
    assert "stock_status" not in url                  # zah's robots.txt disallows it


def test_an_empty_category_is_not_an_error():
    assert FakeStore({1: []}).scrape("gpus") == []


def test_a_failed_page_raises_with_partial_results(make_items):
    store = FakeStore({1: make_items(0, 100), 2: RuntimeError("Failed to fetch: HTTP 500")})
    with pytest.raises(ScrapeIncomplete) as exc:
        store.scrape("gpus")
    assert len(exc.value.partial_results) == 100
    assert "page 2" in str(exc.value)


@pytest.mark.parametrize("body", ['{"code": "rest_error", "message": "no"}', "<html>challenge</html>"])
def test_a_page_that_is_not_a_product_list_is_a_failure(make_items, body):
    store = FakeStore({1: make_items(0, 100), 2: body})
    with pytest.raises(ScrapeIncomplete) as exc:
        store.scrape("gpus")
    assert len(exc.value.partial_results) == 100


def test_host_blocked_propagates_with_partial_results(make_items):
    store = FakeStore({1: make_items(0, 100), 2: HostBlocked("store.test is blocked")})
    with pytest.raises(HostBlocked) as exc:
        store.scrape("gpus")
    assert len(exc.value.partial_results) == 100


def test_max_pages_raises_without_partial_results(make_items):
    class Endless(FakeStore):
        MAX_PAGES = 3

    store = Endless({p: make_items(p * 100, 100) for p in range(1, 4)})
    with pytest.raises(ScrapeIncomplete) as exc:
        store.scrape("gpus")
    assert getattr(exc.value, "partial_results", None) is None


def test_duplicate_urls_are_kept_once(make_items):
    store = FakeStore({1: make_items(0, 100), 2: make_items(99, 2)})
    rows = store.scrape("gpus")
    assert len(rows) == 101
    assert len({r["url"] for r in rows}) == 101
