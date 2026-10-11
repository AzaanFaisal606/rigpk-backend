"""
zah's HTML listing scraper: the default zah source while its Store API is
blocked. The rows it produces must be indistinguishable from the API path's,
above all the URL bytes, since source_id = _slug(url) and a different URL
makes a duplicate part and orphans its price history.
"""
import pytest

from db.database import _slug
from scrapers.zah.html.scraper import BASE, ZahHtmlScraper, category_url


def _card(pid, slug, name, price_text, *, sold_out=False, img="/wp-content/uploads/x.jpg"):
    """One card in the live markup's shape (hash suffixes differ on purpose)."""
    tail = (
        '<span class="ProductCard-module__zz99zz__out">Sold out</span>' if sold_out
        else f'<button type="button" data-add-to-cart="{pid}" data-image="{img}"></button>'
    )
    return f"""
    <article class="ProductCard-module__zz99zz__card" data-product-id="{pid}">
      <a class="ProductCard-module__zz99zz__media" href="/product/{slug}">
        <img src="{img}" alt="{name}" loading="lazy">
      </a>
      <h3 class="ProductCard-module__zz99zz__title">
        <a href="/product/{slug}" title="{name}"> {name} </a>
      </h3>
      <div class="Price-module__qq11qq__price">
        <span class="Price-module__qq11qq__now tabular"> {price_text} </span>
      </div>
      {tail}
    </article>"""


def _page(*cards, total=None):
    hero = f'<strong class="tabular" id="zahHeroTotalCount">{total}</strong> in stock now' if total else ""
    return f"<html><body>{hero}<div class='grid'>{''.join(cards)}</div></body></html>"


# ----------------------------------------------------------------------
# Live fixture: graphics-cards page 1 (24 cards, 3 sold out)
# ----------------------------------------------------------------------

def test_parses_every_in_stock_card_from_fixture(load_fixture):
    html = load_fixture("zah_gpu_page1.html")
    s = ZahHtmlScraper()
    assert len(s.card_blocks(html)) == 24
    products = s._parse_page(html)
    assert len(products) == 21
    for p in products:
        assert p["name"] and p["price_pkr"] > 0
        assert p["source"] == "zahcomputers.pk"


def test_sold_out_cards_are_skipped(load_fixture):
    products = ZahHtmlScraper()._parse_page(load_fixture("zah_gpu_page1.html"))
    urls = {p["url"] for p in products}
    for slug in (
        "pny-geforce-rtx-5060-ti-08gb-stealth-oc-graphics-card",
        "zotac-gaming-geforce-rtx-5090-amp-extreme-infinity-32gb-gddr7-graphics-card",
        "inno3d-geforce-rtx-4070-ti-super-x3-oc-16gb-nvidia-graphic-card",
    ):
        assert f"{BASE}/product/{slug}/" not in urls


def test_price_belongs_to_its_own_product(load_fixture):
    products = {p["name"]: p["price_pkr"]
                for p in ZahHtmlScraper()._parse_page(load_fixture("zah_gpu_page1.html"))}
    expected = {
        "SAPPHIRE NITRO+ AMD Radeon RX 9070 XT OC PhantomLink Polar Edition 16GB GDDR6 "
        "| 256-bit | 3060MHz | PCIe 5.0": 339000,
        "Sapphire Pure Radeon RX 9060 XT 16GB GDDR6 PCI Express 5.0 x16 ATX Graphics Card": 195000,
    }
    for name, price in expected.items():
        assert products[name] == price


def test_url_bytes_match_the_stored_api_permalinks(load_fixture):
    """Cards link /product/<slug>; the API stored https://zahcomputers.pk/product/<slug>/."""
    first = ZahHtmlScraper()._parse_page(load_fixture("zah_gpu_page1.html"))[0]
    stored = ("https://zahcomputers.pk/product/"
              "sapphire-nitro-amd-radeon-rx-9070-xt-oc-phantomlink-polar-edition/")
    assert first["url"] == stored
    assert _slug(first["url"]) == _slug(stored)


def test_thumbnail_is_absolute(load_fixture):
    for p in ZahHtmlScraper()._parse_page(load_fixture("zah_gpu_page1.html")):
        assert p["thumbnail_url"].startswith(f"{BASE}/wp-content/uploads/")


def test_extract_total_from_fixture(load_fixture):
    """The hero count includes sold-out cards, matching ListingScraper's cards_seen."""
    assert ZahHtmlScraper().extract_total(load_fixture("zah_gpu_page1.html")) == 69


# ----------------------------------------------------------------------
# Cases page 1 doesn't carry
# ----------------------------------------------------------------------

def test_contact_without_sold_out_is_skipped():
    html = _page(_card(1, "a", "Priced", "Rs 1,000"),
                 _card(2, "b", "Call us", "Contact"),
                 _card(3, "c", "Free", "Rs 0"))
    products = ZahHtmlScraper()._parse_page(html)
    assert [p["name"] for p in products] == ["Priced"]


def test_double_encoded_entities_decode_like_the_api_path():
    html = _page(_card(1, "ram", "Kingston FURY 32GB XMP 3.0 &amp;amp; AMD EXPO", "Rs 30,000"),
                 _card(2, "mon", "Dell 27&amp;#8243; Monitor ", "Rs 50,000"))
    names = [p["name"] for p in ZahHtmlScraper()._parse_page(html)]
    assert names == ["Kingston FURY 32GB XMP 3.0 & AMD EXPO", "Dell 27″ Monitor"]


def test_extract_total_missing_is_none():
    assert ZahHtmlScraper().extract_total(_page(_card(1, "a", "A", "Rs 1"))) is None


def test_next_page_url():
    s = ZahHtmlScraper()
    url = category_url("graphics-cards")
    assert url == "https://zahcomputers.pk/category/graphics-cards/"
    assert s.next_page_url(url, 1) == url
    assert s.next_page_url(url, 3) == "https://zahcomputers.pk/category/graphics-cards/?page=3"


# ----------------------------------------------------------------------
# Pagination through the template scrape()
# ----------------------------------------------------------------------

def test_scrape_pages_until_the_empty_page():
    """
    Past the last page zah serves a 200 with no cards. Sold-out cards are
    interleaved, and the total counts them, so it never stops early.
    """
    url = category_url("graphics-cards")
    pages = {
        url: _page(_card(1, "p1", "One", "Rs 100"),
                   _card(2, "p2", "Two", "Contact", sold_out=True), total=5),
        f"{url}?page=2": _page(_card(3, "p3", "Three", "Rs 300"),
                               _card(4, "p4", "Four", "Contact", sold_out=True), total=5),
        f"{url}?page=3": _page(_card(5, "p5", "Five", "Rs 500"), total=5),
        f"{url}?page=4": _page(total=5),
    }
    fetched = []

    def fake_fetch(u):
        fetched.append(u)
        if u not in pages:
            pytest.fail(f"fetched past the end: {u}")
        return pages[u]

    s = ZahHtmlScraper()
    s.fetch = fake_fetch
    products = s.scrape(url)

    assert [p["name"] for p in products] == ["One", "Three", "Five"]
    assert all("?" not in p["url"] for p in products)
    assert fetched[0] == url and len(fetched) <= 4


def test_scrape_stops_on_an_empty_page_without_a_total():
    url = category_url("processors")
    pages = {
        url: _page(_card(1, "p1", "One", "Rs 100")),
        f"{url}?page=2": _page(_card(2, "p2", "Two", "Rs 200")),
        f"{url}?page=3": _page(),
    }
    fetched = []

    def fake_fetch(u):
        fetched.append(u)
        return pages[u]

    s = ZahHtmlScraper()
    s.fetch = fake_fetch
    assert [p["name"] for p in s.scrape(url)] == ["One", "Two"]
    assert fetched == [url, f"{url}?page=2", f"{url}?page=3"]
