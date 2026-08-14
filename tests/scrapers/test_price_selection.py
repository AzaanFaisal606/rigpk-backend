"""
On a discounted product the retailer shows two prices: the struck-out original
and the price you actually pay. amdhouse and rbtechngames stored the original —
so every sale item in the catalogue was listed above its real price.
"""
from scrapers.amdhouse.scraper import AmdHouseScraper
from scrapers.rbtechngames.scraper import RbTechNGamesScraper
from scrapers.techmatched.scraper import TechMatchedScraper


# --- amdhouse ---------------------------------------------------------------
# amdhouse_gpu_discounted.html (saved from the itx-graphics-cards category,
# which maps to "gpu") has exactly one in-stock product, and it's on sale:
# "Radeon R7 240 2GB GDDR5 GPU OEM (2x Display Port Output) used 1month wty",
# <del>Rs 6,000</del> <ins>Rs 5,000</ins>. Every other card on the page is out
# of stock, so the "undiscounted product" case is checked at the
# _extract_price level instead (same code path _parse_page calls), against a
# real out-of-stock-but-undiscounted card on the same fixture.

def test_amdhouse_discounted_product_uses_the_sale_price(load_fixture):
    html = load_fixture("amdhouse_gpu_discounted.html")
    products = {p["name"]: p["price_pkr"] for p in AmdHouseScraper()._parse_page(html)}
    name = "Radeon R7 240 2GB GDDR5 GPU OEM (2x Display Port Output) used 1month wty"
    sale, original = 5000, 6000
    assert products[name] == sale
    assert products[name] != original


def test_amdhouse_undiscounted_price_is_unaffected(load_fixture):
    html = load_fixture("amdhouse_gpu_discounted.html")
    import re

    scraper = AmdHouseScraper()
    matches = list(re.finditer(r'<div[^>]+class="[^"]*product-small\s', html))
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else html.find('<footer id="footer"', start)
        blocks.append(html[start:end if end != -1 else len(html)])
    blocks = [b for b in blocks if "woocommerce-loop-product__title" in b]

    target = "Nvidia GeForce ASUS GTX 1660 Super 6GB Single Fan used no box in 1 Week Warranty"
    block = next(b for b in blocks if target in b)
    assert "<del" not in block, "fixture assumption broke: this card is now discounted"
    assert scraper._extract_price(block) == 43000


# --- rbtechngames -------------------------------------------------------------
# rbt_gpu_discounted.html (saved from the power-supplies category — the GPU
# category had no discounted product live at fixture time) has several
# in-stock discounted cards plus in-stock undiscounted ones, so both branches
# are exercised end to end through _parse_page.

def test_rbt_discounted_product_uses_the_sale_price(load_fixture):
    html = load_fixture("rbt_gpu_discounted.html")
    products = {p["name"]: p["price_pkr"] for p in RbTechNGamesScraper()._parse_page(html)}
    name = "SilverStone HELA 1200R 1200W 80+ Platinum ATX 3.1 Fully Modular Power Supply"
    sale, original = 67999, 95000
    assert products[name] == sale
    assert products[name] != original


def test_rbt_undiscounted_product_is_unaffected(load_fixture):
    html = load_fixture("rbt_gpu_discounted.html")
    products = {p["name"]: p["price_pkr"] for p in RbTechNGamesScraper()._parse_page(html)}
    name = "XPG Pylon 650W 80 PLUS Bronze PSU (Power Supply Unit)"
    assert products[name] == 14499


# --- techmatched (M5) ---------------------------------------------------------
# techmatched_price_fallback_synthetic.html is hand-built (see the comment in
# the fixture): a card whose own <li> embeds a related-product widget listing
# a DIFFERENT product's price ahead of the card's own visible price markup.
# The old bare "price": N fallback matched that neighbour's price; it must
# now come only from the JSON dataLayer entry or the card's own visible
# woocommerce-Price-amount markup.

def test_techmatched_fallback_price_does_not_leak_from_a_neighbour(load_fixture):
    html = load_fixture("techmatched_price_fallback_synthetic.html")
    products = {p["name"]: p["price_pkr"] for p in TechMatchedScraper()._parse_page(html)}
    assert products["Plain Card GPU in Pakistan"] == 200000
    assert products["Decoy Card GPU in Pakistan"] == 444999
    assert products["Decoy Card GPU in Pakistan"] != 999999
