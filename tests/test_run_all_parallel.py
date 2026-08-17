"""
Sources run concurrently across hosts, never concurrently within one host.

Per-host pacing is a courtesy contract with real retailers — parallelism must
not turn nine polite crawlers into one impolite one.
"""
import io
import threading
import time
import urllib.parse
import urllib.request

import run_all


def test_sources_run_concurrently(monkeypatch):
    def slow(source, **_kw):
        time.sleep(0.3)
        return {"source": source, "ok": True, "products": 1, "error": None, "results": []}

    monkeypatch.setattr(run_all, "run_source", slow)
    start = time.perf_counter()
    run_all.run_sources(["a", "b", "c", "d"])
    elapsed = time.perf_counter() - start
    assert elapsed < 0.9, f"ran sequentially: {elapsed:.2f}s"


def test_results_are_ordered_deterministically(monkeypatch):
    def varied(source, **_kw):
        time.sleep({"a": 0.3, "b": 0.1, "c": 0.2}[source])
        return {"source": source, "ok": True, "products": 1, "error": None, "results": []}

    monkeypatch.setattr(run_all, "run_source", varied)
    results = run_all.run_sources(["a", "b", "c"])
    assert [r["source"] for r in results] == ["a", "b", "c"]


def test_one_source_failing_does_not_abort_the_others(monkeypatch):
    def flaky(source, **_kw):
        if source == "b":
            raise RuntimeError("boom")
        return {"source": source, "ok": True, "products": 1, "error": None, "results": []}

    monkeypatch.setattr(run_all, "run_source", flaky)
    results = run_all.run_sources(["a", "b", "c"])
    assert len(results) == 3
    assert [r["ok"] for r in results] == [True, False, True]


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _instrument_urlopen(monkeypatch, windows, lock, hold: float):
    """
    Replace urllib.request.urlopen with a stub that records a
    (host, start, end) window around a fixed "network" delay — real enough
    to detect overlapping requests without touching the network.
    """
    def fake_urlopen(req, timeout=None):
        host = urllib.parse.urlsplit(req.full_url).netloc
        start = time.monotonic()
        time.sleep(hold)
        end = time.monotonic()
        with lock:
            windows.append((host, start, end))
        return _FakeResponse(b"<html></html>")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _no_overlap(windows: list[tuple[str, float, float]], host: str) -> bool:
    same_host = [w for w in windows if w[0] == host]
    for i in range(len(same_host)):
        for j in range(i + 1, len(same_host)):
            _, s1, e1 = same_host[i]
            _, s2, e2 = same_host[j]
            if s1 < e2 and s2 < e1:
                return False
    return True


def test_fetch_never_overlaps_for_the_same_host(monkeypatch):
    """
    The hard constraint, proven against the real fetch()/pacing code (not a
    stand-in): instrument urlopen, hammer ONE host from several threads at
    once — worse than the real registry, where every source already has its
    own host — and confirm no two requests to it were ever in flight at the
    same time.
    """
    from scrapers.base_scraper import BaseScraper, reset_host_state

    class TimedScraper(BaseScraper):
        REQUEST_DELAY = 0.05

        def scrape(self, url):
            return []

    reset_host_state()
    windows: list[tuple[str, float, float]] = []
    lock = threading.Lock()
    _instrument_urlopen(monkeypatch, windows, lock, hold=0.05)

    scraper = TimedScraper()
    threads = [
        threading.Thread(target=scraper.fetch, args=(f"https://shared.example.test/p{i}",))
        for i in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    reset_host_state()

    same_host = [w for w in windows if w[0] == "shared.example.test"]
    assert len(same_host) == 5
    assert _no_overlap(windows, "shared.example.test")


def test_fetch_runs_concurrently_across_different_hosts(monkeypatch):
    """
    The other half of the contract: the same-host lock must not become a
    global lock. Requests to different hosts must genuinely overlap, or
    run_sources()'s whole speedup claim is false.
    """
    from scrapers.base_scraper import BaseScraper, reset_host_state

    class TimedScraper(BaseScraper):
        REQUEST_DELAY = 0.05

        def scrape(self, url):
            return []

    reset_host_state()
    windows: list[tuple[str, float, float]] = []
    lock = threading.Lock()
    _instrument_urlopen(monkeypatch, windows, lock, hold=0.2)

    scraper = TimedScraper()
    hosts = [f"https://host{i}.example.test/p" for i in range(4)]
    wall_start = time.perf_counter()
    threads = [threading.Thread(target=scraper.fetch, args=(h,)) for h in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_elapsed = time.perf_counter() - wall_start
    reset_host_state()

    assert len(windows) == 4
    assert wall_elapsed < 0.6, f"different hosts ran serially: {wall_elapsed:.2f}s"
    # At least one pair of distinct-host windows must genuinely overlap.
    any_overlap = any(
        w1[0] != w2[0] and w1[1] < w2[2] and w2[1] < w1[2]
        for i, w1 in enumerate(windows) for w2 in windows[i + 1:]
    )
    assert any_overlap, "different hosts never ran at the same time"


def test_scrape_incomplete_from_one_source_does_not_flip_another_sources_ok(monkeypatch):
    """
    A ScrapeIncomplete (or a HostBlocked) raised while scraping one source
    must only ever affect that source's own `ok` — never a sibling source
    running concurrently on a different host.
    """
    from scrapers.exceptions import ScrapeIncomplete

    def run_a():
        exc = ScrapeIncomplete("a: 2 consecutive page failures")
        exc.partial_results = [{"name": "p", "price_pkr": 1, "url": "u",
                                 "category": "gpu", "source": "a.example.test",
                                 "scraped_at": "t", "thumbnail_url": None}]
        raise exc

    def run_b():
        time.sleep(0.1)  # overlap with a's failure window
        return [{"name": "q", "price_pkr": 1, "url": "u", "category": "gpu",
                  "source": "b.example.test", "scraped_at": "t", "thumbnail_url": None}]

    monkeypatch.setattr(run_all, "SCRAPERS", {
        "a": ("a.example.test", run_a),
        "b": ("b.example.test", run_b),
    })

    results = run_all.run_sources(["a", "b"])
    by_source = {r["source"]: r for r in results}
    assert by_source["a.example.test"]["ok"] is False
    assert "ScrapeIncomplete" in by_source["a.example.test"]["error"]
    assert by_source["b.example.test"]["ok"] is True
    assert by_source["b.example.test"]["error"] is None
