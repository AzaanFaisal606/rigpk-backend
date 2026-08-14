"""
redtech's category-page pagination has the same unbounded shape CZone had
(C4): a `while True` with no page cap and no cap on consecutive fetch
failures. These tests pin all three ways that loop must be able to stop:
a page yielding zero products, an absolute page cap, and a site that keeps
serving the same already-seen products forever.
"""
import pytest

from scrapers.prebuilts.redtech.scraper import RedTechScraper


def _links_page(*slugs: str, next_page: bool = True) -> str:
    anchors = "".join(
        f'<a href="https://redtech.pk/product/{s}/">{s}</a>' for s in slugs
    )
    nav = '<a class="next page-numbers" href="...">Next</a>' if next_page else ""
    return f"<html>{anchors}{nav}</html>"


def test_persistent_fetch_failure_terminates(monkeypatch):
    """An unreachable host must end the run, not spin until the job timeout."""
    import scrapers.prebuilts.redtech.scraper as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    s = RedTechScraper()

    def _boom(*_a, **_k):
        raise OSError("down")

    s.fetch = _boom
    with pytest.raises(Exception) as exc:
        s.scrape_all()
    assert "consecutive page failures" in str(exc.value)


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
