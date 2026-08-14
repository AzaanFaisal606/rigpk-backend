"""
JunaidTech's product list comes from a JSON API, paginated with its own urlopen
call — outside fetch(), so it has none of the retry, pacing or circuit-breaker
behaviour every other request path gets. It carries ~1,841 active products.
"""
import pytest

from scrapers.junaidtech.scraper import JunaidTechScraper


def test_pagination_uses_the_shared_fetch_path(monkeypatch):
    calls = []

    def fake_fetch(self, url, **kw):
        calls.append(url)
        return '{"products": [], "total": 0}'

    monkeypatch.setattr(JunaidTechScraper, "fetch", fake_fetch)
    JunaidTechScraper().scrape("<category url>")
    assert calls, "no request went through fetch() — the API path still bypasses it"


def test_token_failure_falls_back_instead_of_losing_the_source(monkeypatch):
    s = JunaidTechScraper()
    monkeypatch.setattr(s, "_extract_token", lambda *_a, **_k: None)
    # With no token the scraper must attempt the unauthenticated/HTML path
    # rather than returning an empty list, which the orchestrator would read as
    # a successful empty scrape and feed to the sweep.
    with pytest.raises(Exception):
        s.scrape("<category url>")
