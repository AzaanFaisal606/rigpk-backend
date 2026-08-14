"""
redtech's category-page pagination has the same unbounded shape CZone had
(C4): a `while True` with no page cap and no cap on consecutive fetch
failures. These tests pin all the ways that loop must be able to stop:
a page yielding zero products, an absolute page cap, a site that keeps
serving the same already-seen products forever, a 404 marking the end of
pagination, and repeated genuine fetch failures.
"""
import urllib.error

import pytest

from scrapers.prebuilts.redtech.scraper import RedTechScraper


def _links_page(*slugs: str, next_page: bool = True) -> str:
    anchors = "".join(
        f'<a href="https://redtech.pk/product/{s}/">{s}</a>' for s in slugs
    )
    nav = '<a class="next page-numbers" href="...">Next</a>' if next_page else ""
    return f"<html>{anchors}{nav}</html>"


def _product_html(name: str, price: int, cpu: str = "Ryzen 5 5600") -> str:
    """Minimal product-detail page _parse_product can actually parse."""
    return f'''<html>
<h1 class="product_title">{name}</h1>
<script type="application/ld+json">{{"price": "{price}"}}</script>
<table><tr><td>CPU</td><td>{cpu}</td></tr></table>
</html>'''


def test_404_on_page_ends_pagination_keeps_earlier_products(monkeypatch):
    """
    A 404 on the page after the last one is redtech's normal end-of-pagination
    signal, not a failure — the products already collected from earlier pages
    must still come back, not be discarded.
    """
    import scrapers.prebuilts.redtech.scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = RedTechScraper()

    listing_page1 = _links_page("rig-1", "rig-2")

    def _fake_fetch(url):
        if "/product/" in url:
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            return _product_html(f"RedTech {slug} Gaming PC", 150000)
        if url.rstrip("/").endswith("page/2"):
            http_err = urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs=None, fp=None)
            raise RuntimeError(f"Failed to fetch {url}: HTTP 404") from http_err
        return listing_page1

    s.fetch = _fake_fetch
    results = s.scrape_all()

    assert {r["name"] for r in results} == {
        "RedTech rig-1 Gaming PC", "RedTech rig-2 Gaming PC",
    }
    assert len(results) == 2


def test_repeated_genuine_failures_return_collected_products(monkeypatch, capsys):
    """
    Real, repeated fetch errors (not 404s) still bound the loop, but must not
    discard the partial harvest — the caller needs a non-empty result set for
    the sweep to behave, plus a visible warning that the run was incomplete.
    """
    import scrapers.prebuilts.redtech.scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = RedTechScraper()

    listing_page1 = _links_page("rig-1")
    calls = {"listing": 0}

    def _fake_fetch(url):
        if "/product/" in url:
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            return _product_html(f"RedTech {slug} Gaming PC", 99000)
        calls["listing"] += 1
        if calls["listing"] == 1:
            return listing_page1
        raise OSError("connection reset")

    s.fetch = _fake_fetch
    results = s.scrape_all()

    assert [r["name"] for r in results] == ["RedTech rig-1 Gaming PC"]
    assert calls["listing"] == 4  # page 1 ok, pages 2/3/4 fail (budget = 3)
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "consecutive fetch failures" in out


def test_zero_products_page_stops_cleanly(monkeypatch):
    """A page with no product links ends the run without raising."""
    import scrapers.prebuilts.redtech.scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = RedTechScraper()

    calls = {"n": 0}

    def _fake_fetch(_url):
        calls["n"] += 1
        return "<html>no products here</html>"

    s.fetch = _fake_fetch
    results = s.scrape_all()
    assert results == []
    assert calls["n"] == 1


def test_identical_page_every_page_stops_without_raising(monkeypatch):
    """
    A site that keeps serving the same already-seen products (but still
    advertises a next page) must stop once nothing new appears, not loop
    until MAX_PAGES.
    """
    import scrapers.prebuilts.redtech.scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = RedTechScraper()
    s.MAX_PAGES = 50

    same_page = _links_page("rig-1", "rig-2")
    calls = {"listing": 0, "product": 0}

    def _fake_fetch(url):
        if "/product/" in url:
            calls["product"] += 1
        else:
            calls["listing"] += 1
        return same_page   # product-detail fetches get listing markup too — no <h1>, so _parse_product drops them

    s.fetch = _fake_fetch
    results = s.scrape_all()
    assert results == []
    assert calls["listing"] == 2   # page 1 collects rig-1/rig-2, page 2 finds nothing new — stops
    assert calls["product"] == 2   # the 2 collected links still get their (failed) detail fetch


def test_identical_new_content_every_page_hits_the_page_cap(monkeypatch):
    """
    If the site serves a *different* product on every page/offset (so `new`
    never comes back empty), the loop must still stop at MAX_PAGES rather
    than spin forever.
    """
    import scrapers.prebuilts.redtech.scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = RedTechScraper()
    s.MAX_PAGES = 3

    calls = {"n": 0}

    def _fake_fetch(_url):
        calls["n"] += 1
        return _links_page(f"rig-{calls['n']}")

    s.fetch = _fake_fetch
    with pytest.raises(Exception) as exc:
        s.scrape_all()
    assert "MAX_PAGES" in str(exc.value)
    assert calls["n"] == 3
