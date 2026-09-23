"""
PakByteScraper: Shopify products.json -> product rows, and paging.

tests/fixtures/json/shopify_pakbyte_products.json holds whole products picked
from live /collections/<slug>/products.json responses (2026-09-23), unedited.
"""
import copy
import json
import urllib.parse

import pytest

from scrapers.exceptions import ScrapeIncomplete
from scrapers.pakbyte.scraper import PakByteScraper


@pytest.fixture
def products(load_json):
    return {p["handle"]: p for p in load_json("shopify_pakbyte_products.json")["products"]}


def test_in_stock_product_becomes_a_row(products):
    p = products["sapphire-nitro-amd-radeon-rx-9070-xt-oc-16gb-gaming-graphics-card-phantomlink-polar-edition"]
    row = PakByteScraper().parse_item(p)
    assert row["url"] == f"https://www.pakbyte.pk/products/{p['handle']}"
    assert row["name"] == p["title"]
    assert row["price_pkr"] == 249990                           # "249990.00"
    assert row["source"] == "pakbyte.pk"


def test_non_ascii_handle_is_percent_encoded_like_the_storefront(products):
    """
    The listing's hrefs (and so every stored URL) encode ® and ™ as uppercase
    %XX. The raw handle would give _slug() a new source_id: a duplicate part,
    with the old row swept and its price history orphaned.
    """
    p = copy.deepcopy(next(iter(products.values())))
    p["handle"] = "intel®-core™-i9-14900kf-desktop-processor-tray"
    assert PakByteScraper().parse_item(p)["url"] == (
        "https://www.pakbyte.pk/products/intel%C2%AE-core%E2%84%A2-i9-14900kf-desktop-processor-tray"
    )


def test_thumbnail_is_served_from_the_store_domain(products):
    """Same file the listing showed, on the store's own /cdn/shop/ path."""
    p = products["sapphire-nitro-amd-radeon-rx-9070-xt-oc-16gb-gaming-graphics-card-phantomlink-polar-edition"]
    assert p["images"][0]["src"].startswith("https://cdn.shopify.com/s/files/1/0589/8049/9523/files/")
    assert PakByteScraper().parse_item(p)["thumbnail_url"] == (
        "https://www.pakbyte.pk/cdn/shop/files/"
        "sapphire-nitro-amd-radeon-rx-9070-xt-oc-16gb-ppe-price-in-pakistan-pakbyte_1.webp?v=1789368923"
    )


def test_sold_out_product_is_skipped(products):
    p = products["palit-geforce-rtx-5060-ti-oc-16gb-graphics-card-white"]
    assert not any(v["available"] for v in p["variants"])
    assert PakByteScraper().parse_item(p) is None


@pytest.mark.parametrize("handle, price", [
    # both variants available: the cheaper one
    ("sapphire-nitro-amd-radeon-rx-6900-xt-16gb-graphics-card", 118690),
    # the cheaper variant is sold out: the price you can actually pay
    ("xpg-lancer-blade-32gb-2x16gb-6000mhz-c30-ddr5-dram-memory-white", 109999),
    # the pricier variant is sold out
    ("msi-geforce-rtx-4070-ventus-2x-oc-12gb-graphics-card", 189000),
])
def test_multi_variant_price_is_the_cheapest_available(products, handle, price):
    assert len(products[handle]["variants"]) == 2
    assert PakByteScraper().parse_item(products[handle])["price_pkr"] == price


class FakePakByte(PakByteScraper):
    def __init__(self, pages):
        self.pages = pages
        self.requested: list[str] = []

    def fetch(self, url, *a, **k):
        self.requested.append(url)
        page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["page"][0])
        content = self.pages.get(page, [])
        if isinstance(content, Exception):
            raise content
        return json.dumps({"products": content})


@pytest.fixture
def make_products(products):
    base = products["sapphire-nitro-amd-radeon-rx-9070-xt-oc-16gb-gaming-graphics-card-phantomlink-polar-edition"]

    def _make(start, n):
        out = []
        for i in range(start, start + n):
            p = copy.deepcopy(base)
            p["handle"] = f"p{i}"
            out.append(p)
        return out
    return _make


def test_paging_stops_on_a_short_page(make_products):
    scraper = FakePakByte({1: make_products(0, 250), 2: make_products(250, 4)})
    assert len(scraper.scrape("graphic-cards")) == 254
    assert len(scraper.requested) == 2
    assert scraper.requested[0] == "https://www.pakbyte.pk/collections/graphic-cards/products.json?limit=250&page=1"
    assert "sort_by" not in "".join(scraper.requested)       # robots.txt disallows it


def test_a_failed_page_raises_with_partial_results(make_products):
    scraper = FakePakByte({1: make_products(0, 250), 2: RuntimeError("Failed to fetch: HTTP 502")})
    with pytest.raises(ScrapeIncomplete) as exc:
        scraper.scrape("graphic-cards")
    assert len(exc.value.partial_results) == 250
