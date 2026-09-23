"""
An out-of-stock marker found anywhere on the page must not mark every
product on it — the last card's block used to run to the end of the
document (footer, widgets, scripts), so any "out of stock" text anywhere in
that tail silently dropped the last in-stock product of every page.
"""
from scrapers.amdhouse.scraper import AmdHouseScraper
from scrapers.pakbyte.scraper import PakByteScraper
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
        "Radeon R7 240 2GB GDDR5 GPU OEM (2x Display Port Output) used"
    )


def test_rbt_last_block_is_bounded_to_the_footer(load_fixture):
    """Same boundary bug, rbtechngames' own theme markup."""
    html = load_fixture("rbt_gpu_discounted.html")
    products = RbTechNGamesScraper()._parse_page(html)
    assert len(products) == 24


def test_pakbyte_skips_sold_out_cards(load_fixture):
    """
    pakbyte's theme marks a sold-out card with a bare `inventory` span reading
    "Sold out" — no `inventory--out` modifier — so every sold-out product was
    listed at its years-old last price. The fixture page has 24 cards, 23 of
    them sold out.
    """
    html = load_fixture("pakbyte_ram_soldout.html")
    products = PakByteScraper()._parse_page(html)
    assert [p["name"] for p in products] == [
        "G.Skill Trident Z5 RGB 5200MHZ 32GB (16x2) DDR5 Desktop Memory - Metallic Silver"
    ]
    assert "&amp;" not in products[0]["thumbnail_url"]


def test_woodmart_stock_class_belongs_to_its_own_card():
    """
    Woodmart puts `outofstock` on the outer `<div class="wd-product ...">`.
    Splitting on the inner `wd-product-wrapper` handed that token to the
    previous card, so the wrong product was dropped. Inline cards: the live
    zah and techarc listings hide sold-out stock, so no saved page has one.
    """
    from scrapers.zahcomputers.scraper import ZahComputersScraper

    def card(n, stock):
        return (
            f'<div class="wd-product wd-col product type-product {stock}">'
            f'<div class="wd-product-wrapper product-wrapper">'
            f'<a href="https://zahcomputers.pk/product/p{n}/" class="wd-product-img-link" aria-label="Part {n}"></a>'
            f'<span class="woocommerce-Price-amount amount"><bdi><span>Rs</span>{n}0,000</bdi></span>'
            f'</div></div>'
        )

    html = card(1, "instock") + card(2, "outofstock") + card(3, "instock")
    names = [p["name"] for p in ZahComputersScraper()._parse_page(html)]
    assert names == ["Part 1", "Part 3"]


def test_techarc_live_page_parses(load_fixture):
    from scrapers.techarc.scraper import TechArcScraper

    products = TechArcScraper()._parse_page(load_fixture("techarc_gpu_page1.html"))
    assert len(products) == 10
    assert all(p["price_pkr"] and p["thumbnail_url"] for p in products)
