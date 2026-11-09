"""
czone runs on webx (same platform as junaidtech): token + category id from the
SSR page's __NUXT_DATA__, then the JSON listing API with startRow paging.

The bug this replaced: czone's `?page=N` started shifting by one product, so
the HTML pager saw ~1/3 of each category, reported `ok`, and the sweep hid the
rest. These tests pin completeness (a short read must raise, never return),
the stock rule, and URL bytes (source_id continuity).
"""
import copy
import json

import pytest

import scrapers.czone.scraper as mod
from scrapers.czone.scraper import BASE, CzoneAllScraper
from scrapers.exceptions import ScrapeIncomplete

PATH = "/motherboards-pakistan-ppt.157.aspx"
URL = BASE + PATH


@pytest.fixture
def listing(load_json):
    return load_json("webx_czone_motherboard_listing.json")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)


def _scraper(page_html, listing, page_size=None, fail_at=None):
    """A scraper whose fetch serves the SSR fixture and pages the API fixture."""
    rows = listing["data"]["results"]
    calls = []
    s = CzoneAllScraper()

    def fetch(url, headers=None, data=None, **_k):
        if data is None:
            return page_html
        body = json.loads(data)
        calls.append((body, headers))
        start = int(body["startRow"])
        if fail_at is not None and start >= fail_at:
            raise OSError("api down")
        n = page_size or int(body["results"])
        out = copy.deepcopy(listing)
        out["data"]["results"] = rows[start - 1:start - 1 + n]
        return json.dumps(out)

    s.fetch = fetch
    s.calls = calls
    return s


def test_reads_token_and_category_id(load_fixture):
    html = load_fixture("czone_motherboard_page1.html")
    assert CzoneAllScraper.extract_token(html) == "REDACTED.TEST.TOKEN"
    assert CzoneAllScraper.extract_category_id(html, PATH) == "4336084"
    assert CzoneAllScraper.extract_category_id(html, "/not-this-page.aspx") is None


def test_whole_category_and_stock_rule(load_fixture, listing):
    s = _scraper(load_fixture("czone_motherboard_page1.html"), listing)
    out = s.scrape(URL)
    stock = [r["productStock"]["stockStatus"] for r in listing["data"]["results"]]
    assert len(out) == sum(st in ("instock", "onorderonly") for st in stock)
    assert len(out) == 40   # fixture: 22 instock + 18 onorderonly + 3 outofstock
    body, headers = s.calls[0]
    assert body["categoryID"] == "4336084" and body["stockStatus"] == ""
    assert headers["Authorization"] == "Bearer REDACTED.TEST.TOKEN"
    for p in out:
        assert p["source"] == "czone.com.pk" and p["category"] == ""
        assert p["url"].startswith(BASE + "/") and p["price_pkr"] > 0
        assert p["thumbnail_url"].startswith("https://static.webx.pk/")


def test_url_bytes_and_price_come_from_the_same_row(load_fixture, listing):
    """productURL is used as-is (source_id = _slug(url)); price is the selling price."""
    out = {p["url"]: p for p in _scraper(load_fixture("czone_motherboard_page1.html"), listing).scrape(URL)}
    for r in listing["data"]["results"]:
        if r["productStock"]["stockStatus"] == "outofstock":
            assert BASE + r["productURL"] not in out
            continue
        p = out[BASE + r["productURL"]]
        assert p["name"] == r["productName"].strip()
        assert p["price_pkr"] == int(float(r["productPrice"]["price"]))


def test_pages_until_total_even_if_server_caps_page_size(load_fixture, listing):
    """Asked for 100, served 10: paging must advance by rows received, not rows asked."""
    s = _scraper(load_fixture("czone_motherboard_page1.html"), listing, page_size=10)
    assert len(s.scrape(URL)) == 40
    assert [c[0]["startRow"] for c in s.calls] == ["1", "11", "21", "31", "41"]


def test_short_read_raises_instead_of_returning(load_fixture, listing):
    """The old failure: a partial category reported as complete feeds the sweep."""
    trimmed = copy.deepcopy(listing)
    trimmed["data"]["results"] = trimmed["data"]["results"][:20]   # totalRecords still 43
    s = _scraper(load_fixture("czone_motherboard_page1.html"), trimmed)
    with pytest.raises(ScrapeIncomplete) as exc:
        s.scrape(URL)
    assert "20 of 43" in str(exc.value)
    assert exc.value.partial_results


def test_api_failure_mid_listing_keeps_partial(load_fixture, listing, monkeypatch):
    monkeypatch.setattr(mod, "PAGE_SIZE", 20)
    s = _scraper(load_fixture("czone_motherboard_page1.html"), listing, page_size=20, fail_at=21)
    with pytest.raises(ScrapeIncomplete) as exc:
        s.scrape(URL)
    assert 0 < len(exc.value.partial_results) <= 20


def test_missing_token_fails_loudly(listing):
    s = _scraper("<html>no nuxt here</html>", listing)
    with pytest.raises(ScrapeIncomplete):
        s.scrape(URL)
    assert s.calls == []


def test_hidden_price_and_unknown_stock(listing):
    s = CzoneAllScraper()
    row = copy.deepcopy(next(r for r in listing["data"]["results"]
                             if r["productStock"]["stockStatus"] == "instock"))
    row["productPrice"]["isHidePrice"] = "1"
    assert s.parse_item(row)["price_pkr"] is None
    row["productStock"]["stockStatus"] = "discontinued"
    assert s.parse_item(row) is None


def test_persistent_fetch_failure_terminates():
    """An unreachable host must end the run, not spin until the job timeout."""
    s = CzoneAllScraper()

    def _boom(*_a, **_k):
        raise OSError("down")

    s.fetch = _boom
    with pytest.raises(OSError):
        s.scrape(URL)
