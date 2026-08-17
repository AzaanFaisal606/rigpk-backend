"""
An incomplete scrape must never be reported as a successful small scrape.

ScrapeIncomplete is the signal a scraper raises when it could not see
everything it should have (a loop bound was hit, pages kept failing, a token
could not be extracted, or the host blocked us mid-run). The orchestrator
(run_all.py) must:
  - never let ONE category's ScrapeIncomplete get silently absorbed into
    "0 products for that category" while the other categories look clean
    (the routed finding this file exists to pin down — confirmed live in
    run_junaid/run_tech/run_pakbyte/run_redtech/run_techmatched, and in fact
    identical across every run_X() in this file);
  - still upsert whatever products the categories that DID succeed returned;
  - skip the freshness sweep for that source.
"""
import urllib.error

import pytest

from scrapers.exceptions import ScrapeIncomplete
from scrapers.listing_scraper import ListingScraper


# ----------------------------------------------------------------------
# ScrapeIncomplete itself: shared module, HostBlocked is one
# ----------------------------------------------------------------------

def test_junaidtech_reexports_the_shared_exception():
    """
    ScrapeIncomplete used to be defined locally in junaidtech/scraper.py.
    It now lives in scrapers/exceptions.py; junaidtech (and every other
    caller) must still expose/accept the same class, not a lookalike.
    """
    from scrapers.exceptions import ScrapeIncomplete as Shared
    from scrapers.junaidtech.scraper import ScrapeIncomplete as ViaJunaid

    assert ViaJunaid is Shared


def test_host_blocked_propagates_as_scrape_incomplete():
    """
    A host block mid-run must be catchable by `except ScrapeIncomplete`, not
    silently folded into "an ordinary bad page" by a consecutive-failure
    counter that only knows generic Exception.
    """
    from scrapers.base_scraper import HostBlocked

    assert issubclass(HostBlocked, ScrapeIncomplete)
    assert issubclass(HostBlocked, RuntimeError)  # existing callers still catch it


# ----------------------------------------------------------------------
# The routed finding: one category's ScrapeIncomplete must not amputate the
# others, and must not be swallowed into a clean-looking empty return.
# ----------------------------------------------------------------------

def test_run_czone_keeps_other_categories_and_flags_incomplete(monkeypatch):
    import run_all

    def fake_scrape(self, url):
        if "graphic-cards" in url:  # the gpu category's path fragment
            raise ScrapeIncomplete("czone: 3 consecutive page failures")
        return [{
            "name": f"Product for {url}", "price_pkr": 10000, "url": url,
            "category": "", "source": "czone.com.pk", "scraped_at": "t",
            "thumbnail_url": None,
        }]

    monkeypatch.setattr(run_all.CzoneAllScraper, "scrape", fake_scrape)

    with pytest.raises(ScrapeIncomplete) as exc:
        run_all.run_czone()

    partial = exc.value.partial_results
    assert len(partial) == len(run_all.CZONE_CATS) - 1, (
        "every category except the one that raised must still be collected"
    )
    assert all("graphic-cards" not in p["url"] for p in partial), (
        "the failed gpu category's products must not appear in the partial harvest"
    )


def test_run_czone_keeps_partial_results_from_a_failed_category(monkeypatch):
    """
    scrape() itself can raise ScrapeIncomplete with .partial_results set (the
    MAX_CONSECUTIVE_FAILURES case — pages fetched before the fault). run_czone()
    must fold those into its own results, not just the categories that fully
    succeeded, or a category that got 4 good pages before choking on page 5
    contributes nothing at all.
    """
    import run_all

    def fake_scrape(self, url):
        if "graphic-cards" in url:  # the gpu category's path fragment
            exc = ScrapeIncomplete("czone: 3 consecutive page failures")
            exc.partial_results = [{
                "name": "partial gpu product", "price_pkr": 5000, "url": url,
                "category": "", "source": "czone.com.pk", "scraped_at": "t",
                "thumbnail_url": None,
            }]
            raise exc
        return [{
            "name": f"Product for {url}", "price_pkr": 10000, "url": url,
            "category": "", "source": "czone.com.pk", "scraped_at": "t",
            "thumbnail_url": None,
        }]

    monkeypatch.setattr(run_all.CzoneAllScraper, "scrape", fake_scrape)

    with pytest.raises(ScrapeIncomplete) as exc:
        run_all.run_czone()

    partial = exc.value.partial_results
    assert len(partial) == len(run_all.CZONE_CATS), (
        "the failed category's own partial harvest must be folded in "
        "alongside every category that fully succeeded"
    )
    gpu_rows = [p for p in partial if p["name"] == "partial gpu product"]
    assert len(gpu_rows) == 1
    assert gpu_rows[0]["category"] == "gpu"


def test_incomplete_run_is_not_ok_and_does_not_sweep(tmp_path, monkeypatch):
    """
    Full path through the real entry point (main()): a source where 1 of N
    categories raises ScrapeIncomplete and the rest succeed must (a) upsert
    the products the successful categories returned and (b) not run the
    freshness sweep — the 10 pre-existing rows must survive untouched.
    """
    import sys

    import run_all
    from db.database import Database, get_db

    db_path = tmp_path / "t.db"
    seed = Database(db_path)
    seed.upsert_products([{
        "name": f"GPU {i}", "price_pkr": 50000,
        "url": f"https://czone.com.pk/product/gpu-{i}", "category": "gpu",
        "source": "czone.com.pk", "scraped_at": "2026-08-01T00:00:00Z",
        "thumbnail_url": None,
    } for i in range(10)])
    seed.close()

    def fake_run_czone():
        exc = ScrapeIncomplete("czone: 3 consecutive page failures")
        exc.partial_results = [{
            "name": "New GPU X", "price_pkr": 60000,
            "url": "https://czone.com.pk/product/new-gpu-x", "category": "gpu",
            "source": "czone.com.pk", "scraped_at": "2026-08-01T00:00:00Z",
            "thumbnail_url": None,
        }]
        raise exc

    monkeypatch.setattr(run_all, "DB_PATH", str(db_path))
    monkeypatch.setattr(run_all, "SCRAPERS", {"czone": ("czone.com.pk", fake_run_czone)})
    monkeypatch.setattr(run_all, "backup_db", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", ["run_all.py", "czone"])

    run_all.main()

    with get_db(str(db_path)) as db:
        health = db.source_health()["czone.com.pk"]
        active = db.stats()["by_source"]["czone.com.pk"]

    assert health["stale"] is True, "an incomplete run must mark the source stale, not clean"
    assert "ScrapeIncomplete" in health["last_error"]
    assert active == 11, (
        "10 pre-existing + 1 collected before the fault; a real sweep would "
        "have deactivated the other 9 pre-existing rows never re-seen"
    )


def test_prebuilt_incomplete_run_upserts_partial_results_and_does_not_sweep(tmp_path, monkeypatch):
    """
    run_prebuilts.py regression: a prebuilt source whose scrape_all() raises
    ScrapeIncomplete carrying .partial_results must still upsert those
    products (previously the bare `except Exception` swallowed the
    exception, results stayed [], and the partial data was dropped) while
    still skipping the freshness sweep — the 5 pre-existing rows for this
    source must survive untouched.
    """
    import sys

    from scrapers.prebuilts import run_prebuilts
    from db.database import Database, get_db

    (tmp_path / "data").mkdir()
    db_path = tmp_path / "data" / "ppc.db"
    seed = Database(db_path)
    seed.upsert_prebuilts([{
        "name": f"Prebuilt {i}", "price_pkr": 200000,
        "url": f"https://redtech.pk/product/pc-{i}", "source": "redtech.pk",
        "scraped_at": "2026-08-01T00:00:00Z", "thumbnail_url": None,
        "components": None,
    } for i in range(5)])
    seed.close()

    class FakeIncompleteScraper:
        def scrape_all(self):
            exc = ScrapeIncomplete("redtech: 3 consecutive page failures")
            exc.partial_results = [{
                "name": "New Prebuilt X", "price_pkr": 250000,
                "url": "https://redtech.pk/product/new-pc-x", "source": "redtech.pk",
                "scraped_at": "2026-08-01T00:00:00Z", "thumbnail_url": None,
                "components": None,
            }, {
                "name": "New Prebuilt Y", "price_pkr": 260000,
                "url": "https://redtech.pk/product/new-pc-y", "source": "redtech.pk",
                "scraped_at": "2026-08-01T00:00:00Z", "thumbnail_url": None,
                "components": None,
            }]
            raise exc

    monkeypatch.setattr(run_prebuilts, "SCRAPERS", {
        "redtech": ("redtech.pk", FakeIncompleteScraper),
    })
    monkeypatch.setattr(run_prebuilts, "ROOT", str(tmp_path))
    monkeypatch.setattr(run_prebuilts, "backup_db", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", ["run_prebuilts.py", "redtech"])

    run_prebuilts.main()

    with get_db(str(db_path)) as db:
        health = db.source_health(kind="prebuilt")["redtech.pk"]
        active = db.prebuilt_stats()["by_source"]["redtech.pk"]

    assert active == 7, (
        "5 pre-existing + 2 collected before the fault must all be upserted; "
        "a real sweep would have deactivated the 5 pre-existing rows never re-seen"
    )
    assert health["stale"] is True, "an incomplete run must mark the source stale, not clean"
    assert "ScrapeIncomplete" in health["last_error"]


# ----------------------------------------------------------------------
# H9 — amdhouse's probe: a transient failure is not "category doesn't exist"
# ----------------------------------------------------------------------

def test_amdhouse_probe_network_error_is_not_read_as_category_missing(monkeypatch):
    from scrapers.amdhouse import scraper as amd_mod

    monkeypatch.setattr(amd_mod, "CATEGORIES", [
        ("graphics-cards", "gpu"),
        ("retired-category", "gpu"),
        ("processors", "cpu"),
    ])
    monkeypatch.setattr(amd_mod.time, "sleep", lambda *_a, **_k: None)

    def fake_fetch(self, url):
        if "retired-category" in url:
            raise urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs=None, fp=None)
        if "processors" in url:
            raise OSError("connection reset")
        return "<div class='woocommerce-loop-product__title'></div>"

    monkeypatch.setattr(amd_mod.AmdHouseScraper, "fetch", fake_fetch)

    with pytest.raises(ScrapeIncomplete) as exc:
        amd_mod._find_valid_categories()

    assert "processors" in str(exc.value)
    assert "retired-category" not in str(exc.value), "a genuine 404 must not be reported as a probe error"
    assert [cat for _url, cat in exc.value.valid_categories] == ["gpu"]


def test_amdhouse_probe_all_404s_returns_cleanly(monkeypatch):
    """The original, correct case: every slug 404s -> empty list, no exception."""
    from scrapers.amdhouse import scraper as amd_mod

    monkeypatch.setattr(amd_mod, "CATEGORIES", [("retired-a", "gpu"), ("retired-b", "cpu")])
    monkeypatch.setattr(amd_mod.time, "sleep", lambda *_a, **_k: None)

    def fake_fetch(self, url):
        raise urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(amd_mod.AmdHouseScraper, "fetch", fake_fetch)

    assert amd_mod._find_valid_categories() == []


# ----------------------------------------------------------------------
# H8 — a stalled keep-alive read must not outlive a per-chunk timeout=
# ----------------------------------------------------------------------

def test_read_deadline_bounds_a_stalled_trickling_body(monkeypatch):
    """
    urlopen's timeout= bounds connect and each individual .read() call, but a
    connection that keeps returning small chunks forever never trips it —
    that's zestro's hang. READ_DEADLINE is a wall-clock cap across the whole
    body read, so it must fire even though every .read() call "succeeds".
    """
    import itertools
    import urllib.request

    from scrapers import base_scraper
    from scrapers.base_scraper import BaseScraper, reset_host_state

    class TrickleResponse:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _n):
            return self._chunks.pop(0) if self._chunks else b""

    ticks = itertools.count()
    monkeypatch.setattr(base_scraper.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: TrickleResponse([b"a", b"b", b"c"]))

    class DummyScraper(BaseScraper):
        REQUEST_DELAY = 0
        READ_DEADLINE = 0  # any forward tick of the (fake, always-increasing) clock trips it

        def scrape(self, url):
            return []

    reset_host_state()
    with pytest.raises(RuntimeError, match="READ_DEADLINE"):
        DummyScraper().fetch("https://trickle.test/x", retries=1)
    reset_host_state()


# ----------------------------------------------------------------------
# F6 — a page fetch that failed must not be reported as "a page with
# nothing new on it" just because a later page loaded fine and the
# consecutive-failure counter reset. Left unfixed, ListingScraper.scrape()
# returns normally (no exception at all), run_all's `ok = bool(results)
# and error is None` reads that as a clean success, and
# deactivate_unseen_parts then delists every product the skipped page
# would have contributed.
# ----------------------------------------------------------------------


class _SkipPageScraper(ListingScraper):
    SOURCE = "faketest"

    def __init__(self, pages):
        # pages: {page_number: "a|b|c" (card-block string) or an Exception
        # instance to raise on fetch}
        self._pages = pages

    def card_blocks(self, html):
        return html.split("|") if html else []

    def parse_card(self, block):
        return {
            "name": block, "price_pkr": 1000, "url": f"https://example.com/{block}",
            "category": "gpu", "source": self.SOURCE, "scraped_at": "t",
        }

    def fetch(self, url):
        page = 1 if "?page=" not in url else int(url.rsplit("=", 1)[1])
        content = self._pages.get(page, "")
        if isinstance(content, Exception):
            raise content
        return content


def test_a_failed_page_is_not_reported_as_a_clean_end_of_listing():
    """
    Page 1 succeeds (2 products). Page 2's fetch fails once (under
    MAX_CONSECUTIVE_FAILURES, so the old code just `continue`s). Page 3
    succeeds but re-serves the SAME products as page 1 -- zero NEW items,
    which is the normal "end of listing" signal and used to `break` with no
    exception at all. The page 2 products were never seen by anyone.
    """
    scraper = _SkipPageScraper({
        1: "a|b",
        2: RuntimeError("page 2 fetch failed"),
        3: "a|b",  # already-seen -> new == 0 -> the loop's normal exit
    })

    with pytest.raises(ScrapeIncomplete) as exc:
        scraper.scrape("https://example.com/cat")

    partial = exc.value.partial_results
    assert [p["name"] for p in partial] == ["a", "b"], (
        "partial_results must hold exactly what page 1 collected before "
        "the skipped page"
    )


def test_no_skipped_pages_still_returns_normally():
    """Control case: nothing was skipped, so the normal empty-`break` exit
    must still return the product list, not raise."""
    scraper = _SkipPageScraper({
        1: "a|b",
        2: "a|b",  # already-seen on page 2 -> new == 0 -> clean end
    })

    products = scraper.scrape("https://example.com/cat")

    assert [p["name"] for p in products] == ["a", "b"]


def test_max_pages_raise_still_carries_no_partial_results():
    """
    Deliberate asymmetry, preserved: MAX_PAGES exhaustion means the site
    kept serving "new" content forever (or is broken) -- trust nothing
    collected, unlike the failed-page case above which trusts what came
    before the gap.
    """
    pages = {p: f"item{p}" for p in range(1, ListingScraper.MAX_PAGES + 1)}
    scraper = _SkipPageScraper(pages)

    with pytest.raises(ScrapeIncomplete) as exc:
        scraper.scrape("https://example.com/cat")

    assert not hasattr(exc.value, "partial_results") or exc.value.partial_results is None
