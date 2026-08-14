"""
CZone parses names, prices and URLs from three separate passes and zips them by
list index. Any skew silently attaches the wrong price to the wrong product —
the output stays well-formed, so nothing downstream can detect it.

These tests pin per-product association and bound the pagination loop.
"""
import re

import pytest

from scrapers.czone.all_scraper import CzoneAllScraper


def test_parses_products_from_fixture(load_fixture):
    html = load_fixture("czone_gpu_page1.html")
    products = CzoneAllScraper()._parse_page(html)
    assert len(products) > 0
    for p in products:
        assert p["name"]
        assert p["url"].startswith("http")
        assert p["price_pkr"] is None or p["price_pkr"] > 0


def test_price_belongs_to_its_own_product(load_fixture):
    """
    Every product's price must come from the same card as its name. Pinning a
    few known (name, price) pairs from the fixture catches index skew, which is
    invisible to a count-based check.
    """
    html = load_fixture("czone_gpu_page1.html")
    products = {p["name"]: p["price_pkr"] for p in CzoneAllScraper()._parse_page(html)}
    # Pinned from the fixture itself (first and last cards on the page).
    expected = {
        "MSI GeForce RTX 3050 VENTUS 2X 6G OC Video Graphics Card, 6GB GDDR6, "
        "Boost Clock 1492MHz, 14Gbps, HDMI 2.1 4K@120Hz, DisplayPort 1.4a, "
        "Dual Fan Cooling": 95000,
        "MSI GeForce RTX 5070 12G VENTUS 2X OC WHITE Graphics Card, 12GB GDDR7 "
        "192-Bit, PCI Express Gen 5, 2557MHz Extreme Performance, DisplayPort x 3 "
        "v2.1b and HDMI 2.1b, White G5070-12V2CW": 247000,
    }
    for name, price in expected.items():
        assert products[name] == price


def test_a_card_missing_a_price_does_not_shift_its_neighbours(load_fixture):
    """
    The failure mode: one card has no price node, the price list is shorter than
    the name list, and every subsequent product takes its neighbour's price.
    """
    html = load_fixture("czone_gpu_page1.html")
    products = CzoneAllScraper()._parse_page(html)
    baseline = {p["name"]: p["price_pkr"] for p in products}

    # Remove the first price node from the markup and re-parse.
    damaged = re.sub(r'<div class="product-price">.*?</div>', "", html,
                      count=1, flags=re.DOTALL)
    after = {p["name"]: p["price_pkr"] for p in CzoneAllScraper()._parse_page(damaged)}

    shifted = [n for n in after if n in baseline
               and after[n] is not None
               and baseline[n] is not None
               and after[n] != baseline[n]]
    assert not shifted, f"prices shifted onto the wrong products: {shifted[:5]}"


def test_persistent_fetch_failure_terminates(monkeypatch):
    """An unreachable host must end the run, not spin until the job timeout."""
    import scrapers.czone.all_scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = CzoneAllScraper()

    def _boom(*_a, **_k):
        raise OSError("down")

    s.fetch = _boom
    with pytest.raises(Exception) as exc:
        s.scrape("https://www.czone.com.pk/graphic-cards-pakistan-ppt.154.aspx")
    assert "consecutive page failures" in str(exc.value)


def test_identical_page_every_offset_hits_the_page_cap(monkeypatch, load_fixture):
    """
    If the site ever serves the same page regardless of ?page=N (new products
    each time so `new` never comes back empty), the loop must still stop at
    MAX_PAGES rather than spin forever.
    """
    import scrapers.czone.all_scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    html = load_fixture("czone_gpu_page1.html")
    s = CzoneAllScraper()
    s.MAX_PAGES = 3   # keep the test fast; behaviour is the same at 200

    calls = {"n": 0}

    def _fake_fetch(_url):
        calls["n"] += 1
        return html

    s.fetch = _fake_fetch
    # Total-count short-circuit would otherwise stop the loop early; force it off.
    monkeypatch.setattr(s, "_extract_total", lambda _html: None)
    # Every "page" looks brand new (different URLs) so `new` is never empty.
    monkeypatch.setattr(
        s, "_parse_page",
        lambda _html: [{"name": f"p{calls['n']}", "price_pkr": 1,
                         "url": f"http://x/{calls['n']}", "category": "",
                         "source": "czone.com.pk", "scraped_at": "t",
                         "thumbnail_url": None}],
    )

    with pytest.raises(Exception) as exc:
        s.scrape("https://www.czone.com.pk/graphic-cards-pakistan-ppt.154.aspx")
    assert "MAX_PAGES" in str(exc.value)
    assert calls["n"] == 3
