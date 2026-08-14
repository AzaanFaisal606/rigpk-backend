"""
HTML entities must be decoded in names, in exactly one place, exactly once.
amdhouse decoded nothing at all (raw "&#8211;", "&amp;" landed in
parts.name); rbtechngames hand-decoded three entities and missed the rest
(e.g. "&#8243;", "&#215;"). Both now go through a single html.unescape().

The second assertion in each test guards the opposite failure: decoding
twice, which turns a literal "&amp;" in a product name into "&".
"""
import html as htmllib

from scrapers.amdhouse.scraper import AmdHouseScraper
from scrapers.rbtechngames.scraper import RbTechNGamesScraper
from scrapers.techmatched.scraper import TechMatchedScraper


def test_amdhouse_entities_are_decoded_once(load_fixture):
    html = load_fixture("amdhouse_gpu_discounted.html")
    names = [p["name"] for p in AmdHouseScraper()._parse_page(html)]
    assert names, "fixture yielded no products to check"
    for n in names:
        assert "&amp;" not in n and "&#" not in n, f"undecoded entity in {n!r}"
        assert htmllib.unescape(n) == n, f"double-decoding changed {n!r}"


def test_amdhouse_decodes_entities_the_old_code_never_touched():
    """
    amdhouse never called html.unescape at all — raw entities landed in
    parts.name. Inline card (not a saved fixture — this is a targeted unit
    check, not evidence of retailer markup): a real amdhouse title that has
    both "&amp;" and the numeric "&#8243;" (double-prime, used for inches).
    """
    card = (
        '<div class="product-small box ">'
        '<p class="name product-title woocommerce-loop-product__title">'
        '<a href="https://amdhouse.pk/product/test/" '
        'class="woocommerce-LoopProduct-link woocommerce-loop-product__link">'
        "Thermalright RL-M10 Vision mATX PC Case with 4x Infinity Mirror Fans "
        "&amp; Customizable 9&#8243; LCD Display (Black)</a></p>"
        '<div class="price-wrapper"><span class="price">'
        '<span class="woocommerce-Price-amount amount"><bdi>'
        '<span class="woocommerce-Price-currencySymbol" translate="no">&#8360;</span>'
        "25,000</bdi></span></span></div></div>"
    )
    products = AmdHouseScraper()._parse_page(card)
    assert products[0]["name"] == (
        "Thermalright RL-M10 Vision mATX PC Case with 4x Infinity Mirror Fans "
        "& Customizable 9″ LCD Display (Black)"
    )


def test_rbt_entities_are_decoded_once(load_fixture):
    html = load_fixture("rbt_gpu_discounted.html")
    names = [p["name"] for p in RbTechNGamesScraper()._parse_page(html)]
    assert names, "fixture yielded no products to check"
    assert any("–" in n for n in names), (
        "fixture assumption broke: expected at least one name with a decoded "
        "en dash (source has literal &#8211;)"
    )
    for n in names:
        assert "&amp;" not in n and "&#" not in n, f"undecoded entity in {n!r}"
        assert htmllib.unescape(n) == n, f"double-decoding changed {n!r}"


def test_rbt_decodes_entities_the_old_manual_replace_missed():
    """
    rbt's old fix hand-decoded exactly three entities (&#8211;, &amp;, &#039;)
    and missed everything else. Inline card (not a saved fixture — a targeted
    unit check): a real rbt monitor title with "&#8243;" (inches) and
    "&#215;" (multiplication sign), neither of which was in the old list.
    """
    card = (
        '<div class="product-small box ">'
        '<p class="name product-title woocommerce-loop-product__title">'
        '<a href="https://rbtechngames.com/product/test/" '
        'class="woocommerce-LoopProduct-link woocommerce-loop-product__link">'
        "MSI MAG 255F E20, 25&#8243; (1920&#215;1080) FHD 200Hz Gaming Monitor</a></p>"
        '<div class="price-wrapper"><span class="price">'
        '<span class="woocommerce-Price-amount amount"><bdi>'
        '<span class="woocommerce-Price-currencySymbol" translate="no">&#8360;</span>'
        "45,000</bdi></span></span></div></div>"
    )
    products = RbTechNGamesScraper()._parse_page(card)
    assert products[0]["name"] == (
        "MSI MAG 255F E20, 25″ (1920×1080) FHD 200Hz Gaming Monitor"
    )


def test_techmatched_entities_are_decoded_once(load_fixture):
    html = load_fixture("techmatched_gpu_page1.html")
    names = [p["name"] for p in TechMatchedScraper()._parse_page(html)]
    assert names, "fixture yielded no products to check"
    for n in names:
        assert "&amp;" not in n and "&#" not in n, f"undecoded entity in {n!r}"
        assert htmllib.unescape(n) == n, f"double-decoding changed {n!r}"
