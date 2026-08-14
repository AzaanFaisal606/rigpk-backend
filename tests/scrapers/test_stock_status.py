"""
An out-of-stock marker found anywhere on the page must not mark every
product on it — the last card's block used to run to the end of the
document (footer, widgets, scripts), so any "out of stock" text anywhere in
that tail silently dropped the last in-stock product of every page.
"""
from scrapers.amdhouse.scraper import AmdHouseScraper
from scrapers.rbtechngames.scraper import RbTechNGamesScraper
from scrapers.techmatched.scraper import TechMatchedScraper


def test_techmatched_status_does_not_leak_from_a_later_product(load_fixture):
    """
    The last card on a page previously carried the rest of the document with
    it, so an "out of stock" banner in the page footer marked it unavailable.
    """
    html = load_fixture("techmatched_gpu_page1.html")
    products = TechMatchedScraper()._parse_page(html)
    in_stock = [p for p in products if p["price_pkr"] is not None]
    assert len(in_stock) > 1, "every product reading as OOS is the bug"


def test_amdhouse_last_block_is_bounded_to_the_footer(load_fixture):
    """
    amdhouse_gpu_discounted.html has 12 product cards; 11 are genuinely
    out of stock (their own outer wrapper carries the "outofstock" class) and
    exactly one — the last card on the page — is genuinely in stock. Before
    the fix, that last card's block ran past its own boundary into the
    footer/widget tail, which also contains the literal text "out-of-stock",
    so it was wrongly dropped and the page returned zero products.
    """
    html = load_fixture("amdhouse_gpu_discounted.html")
    products = AmdHouseScraper()._parse_page(html)
    assert len(products) == 1
    assert products[0]["name"] == (
        "Radeon R7 240 2GB GDDR5 GPU OEM (2x Display Port Output) used 1month wty"
    )


def test_rbt_last_block_is_bounded_to_the_footer(load_fixture):
    """Same boundary bug, rbtechngames' own theme markup."""
    html = load_fixture("rbt_gpu_discounted.html")
    products = RbTechNGamesScraper()._parse_page(html)
    assert len(products) == 24
