"""
JunaidTech's product list comes from a JSON API, paginated with its own urlopen
call — outside fetch(), so it has none of the retry, pacing or circuit-breaker
behaviour every other request path gets. It carries ~1,841 active products.
"""
import pytest

from scrapers.junaidtech.scraper import API, JunaidTechScraper, ScrapeIncomplete


def test_pagination_uses_the_shared_fetch_path(monkeypatch):
    calls = []

    def fake_fetch(self, url, **kw):
        calls.append(url)
        return '{"products": [], "total": 0}'

    monkeypatch.setattr(JunaidTechScraper, "fetch", fake_fetch)
    JunaidTechScraper().scrape("<category url>")
    assert calls, "no request went through fetch() — the API path still bypasses it"


def test_token_missing_raises_scrape_incomplete_not_empty_list(monkeypatch):
    """
    _extract_token finds nothing on the SSR page (page-format change, or a
    genuinely token-less response). scrape() must still get past the SSR
    fetch() call (real path, mocked at fetch()) and reach the pagination
    loop, where the first (unauthenticated) API call also fails — this is
    the exact real-world shape: no token AND the API call that follows fails.

    That combination must raise ScrapeIncomplete, never return []: run_all.py
    reads [] as "this source has 0 products right now" and feeds it to the
    freshness sweep, deactivating ~1,841 active rows.
    """
    calls = []

    def fake_fetch(self, url, **kw):
        calls.append(url)
        if url == API:
            raise RuntimeError("simulated: unauthenticated API call rejected")
        return "<html>no __NUXT_DATA__ here</html>"  # SSR page

    monkeypatch.setattr(JunaidTechScraper, "fetch", fake_fetch)
    s = JunaidTechScraper()
    monkeypatch.setattr(s, "_extract_token", lambda *_a, **_k: None)

    result = None
    try:
        result = s.scrape("<category url>", known_category_id="123", category="gpu")
    except ScrapeIncomplete as e:
        assert "token" in str(e).lower(), f"expected the missing token to be named in the error, got: {e}"
    else:
        pytest.fail(
            "scrape() returned "
            f"{result!r} instead of raising ScrapeIncomplete on a missing token — "
            "a [] return here is silently read by run_all.py as \"0 products\" "
            "and would feed the freshness sweep, deactivating every active row."
        )
    assert result is None, "scrape() must not have produced a return value on this path"
    assert API in calls, "the (failing) API call must actually have been attempted"


def test_token_present_but_api_rejects_raises_scrape_incomplete(monkeypatch):
    """
    Token extraction succeeds, but the API call itself fails (e.g. the token
    is stale/rejected — a real fetch() would see this as an HTTP 401, which
    is in NO_RETRY_CODES and raises RuntimeError). The first page failing
    with nothing collected yet must still raise ScrapeIncomplete, not swallow
    the failure into an empty result.
    """
    calls = []

    def fake_fetch(self, url, **kw):
        calls.append(url)
        if url == API:
            raise RuntimeError(f"Failed to fetch {API}: HTTP 401")
        return "<html>no __NUXT_DATA__ here</html>"  # SSR page

    monkeypatch.setattr(JunaidTechScraper, "fetch", fake_fetch)
    s = JunaidTechScraper()
    monkeypatch.setattr(s, "_extract_token", lambda *_a, **_k: "looks-valid-but-isnt")

    with pytest.raises(ScrapeIncomplete, match="pagination failed"):
        s.scrape("<category url>", known_category_id="123", category="gpu")

    assert API in calls, "the rejected API call must actually have been attempted"
