"""
CZone parses names, prices and URLs from three separate passes and zips them by
list index. Any skew silently attaches the wrong price to the wrong product —
the output stays well-formed, so nothing downstream can detect it.

These tests pin per-product association and bound the pagination loop.
"""
import re

import pytest

from scrapers.czone.all_scraper import CzoneAllScraper
from scrapers.exceptions import ScrapeIncomplete


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
    with pytest.raises(ScrapeIncomplete) as exc:
        s.scrape("https://www.czone.com.pk/graphic-cards-pakistan-ppt.154.aspx")
    assert "consecutive page failures" in str(exc.value)


def test_page_failure_after_success_preserves_earlier_pages(monkeypatch):
    """
    Pages 1-2 succeed and contribute products; page 3 then fails
    MAX_CONSECUTIVE_FAILURES times in a row. The raise must carry forward
    everything already scraped from pages 1-2 on .partial_results, not
    discard it (the earlier bug: only the outer run_czone() wrapping in
    run_all.py preserved data, this internal raise site didn't).
    """
    import scrapers.czone.all_scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = CzoneAllScraper()

    page1_products = [{"name": "p1", "price_pkr": 100, "url": "http://x/1",
                        "category": "", "source": "czone.com.pk",
                        "scraped_at": "t", "thumbnail_url": None}]
    page2_products = [{"name": "p2", "price_pkr": 200, "url": "http://x/2",
                        "category": "", "source": "czone.com.pk",
                        "scraped_at": "t", "thumbnail_url": None}]
    parse_map = {"page1": page1_products, "page2": page2_products}

    fetch_calls = {"n": 0}

    def _fake_fetch(_url):
        fetch_calls["n"] += 1
        if fetch_calls["n"] <= 2:
            return f"page{fetch_calls['n']}"
        raise OSError("down")

    s.fetch = _fake_fetch
    monkeypatch.setattr(s, "_extract_total", lambda _html: None)
    monkeypatch.setattr(s, "_parse_page", lambda html: parse_map.get(html, []))

    with pytest.raises(ScrapeIncomplete) as exc:
        s.scrape("https://www.czone.com.pk/graphic-cards-pakistan-ppt.154.aspx")

    assert "consecutive page failures" in str(exc.value)
    assert exc.value.partial_results == page1_products + page2_products


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

    with pytest.raises(ScrapeIncomplete) as exc:
        s.scrape("https://www.czone.com.pk/graphic-cards-pakistan-ppt.154.aspx")
    assert "MAX_PAGES" in str(exc.value)
    assert calls["n"] == 3
    # Deliberate asymmetry (matches redtech's MAX_PAGES raise): a page cap
    # this generous being hit at all means the collected rows aren't trusted
    # either, so no .partial_results is attached here.
    assert not hasattr(exc.value, "partial_results")


def _http_404(url="http://x/page/2"):
    """The exact shape BaseScraper.fetch() raises for a 404: a RuntimeError
    whose __cause__ is the HTTPError (404 is in NO_RETRY_CODES)."""
    import urllib.error
    cause = urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    err = RuntimeError(f"Failed to fetch {url}: HTTP 404")
    err.__cause__ = cause
    return err


def test_a_404_past_page_one_ends_the_listing_cleanly(monkeypatch):
    """
    A WooCommerce category with fewer pages than the loop tries returns 404
    for page N+1 — that is the end of the listing, not a fault. Counting it
    as a failure made every short category fail deterministically:
    techmatched's SSD listing is one page of 16 products, so pages 2/3/4 all
    404 and tripped MAX_CONSECUTIVE_FAILURES on every single run.
    """
    import scrapers.czone.all_scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = CzoneAllScraper()

    page1 = [{"name": "p1", "price_pkr": 100, "url": "http://x/1",
              "category": "", "source": "czone.com.pk",
              "scraped_at": "t", "thumbnail_url": None}]
    calls = {"n": 0}

    def _fake_fetch(_url):
        calls["n"] += 1
        if calls["n"] == 1:
            return "page1"
        raise _http_404()

    s.fetch = _fake_fetch
    monkeypatch.setattr(s, "_extract_total", lambda _html: None)
    monkeypatch.setattr(s, "_parse_page", lambda html: page1 if html == "page1" else [])

    products = s.scrape("https://www.czone.com.pk/graphic-cards-pakistan-ppt.154.aspx")

    assert products == page1, "page 1's harvest must be returned, not discarded"
    assert calls["n"] == 2, "the 404 must stop the loop immediately, not retry pages 3 and 4"


def test_a_404_on_page_one_is_still_a_failure(monkeypatch):
    """
    A dead category URL is a real fault — only a 404 PAST page 1 means "the
    listing ended". Without this distinction a mistyped or retired category
    would scrape zero products and report success, which then lets the
    freshness sweep delist everything that category holds.
    """
    import scrapers.czone.all_scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = CzoneAllScraper()
    s.fetch = lambda _url: (_ for _ in ()).throw(_http_404("http://x/dead"))

    with pytest.raises(ScrapeIncomplete) as exc:
        s.scrape("https://www.czone.com.pk/graphic-cards-pakistan-ppt.154.aspx")

    # Page 1's 404 counts as a real failure and sets the sticky
    # `pages_skipped` flag, so the run still raises even though the page-2
    # 404 is what breaks the loop. Asserting the type, not the wording: what
    # must hold is that this never returns cleanly — a clean return is
    # precisely what lets the freshness sweep delist the whole category.
    assert isinstance(exc.value, ScrapeIncomplete)
    assert not getattr(exc.value, "partial_results", None)
