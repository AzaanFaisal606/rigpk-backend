"""
Tests for BaseScraper.fetch() retry / backoff / circuit-breaker behaviour.

Network is never touched: urlopen is replaced with a scripted stub and sleeps
are neutered, so a run that would take minutes in the wild finishes instantly.
"""

import io
import urllib.error
import urllib.request

import pytest

from scrapers import base_scraper
from scrapers.base_scraper import BaseScraper, HostBlocked, blocked_hosts, reset_host_state

URL = "https://example.test/product-category/ram/"
OTHER = "https://other.test/shop/"


class DummyScraper(BaseScraper):
    REQUEST_DELAY = 0

    def scrape(self, url: str) -> list[dict]:
        return []


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    import email.message

    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError(URL, code, f"HTTP {code}", msg, None)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Fresh breaker state per test, and no real sleeping."""
    reset_host_state()
    monkeypatch.setattr(base_scraper.time, "sleep", lambda *_: None)
    yield
    reset_host_state()


def script(monkeypatch, outcomes):
    """Make urlopen yield `outcomes` in order; each is bytes or an exception."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        out = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(out, Exception):
            raise out
        return FakeResponse(out)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_returns_body_on_first_success(monkeypatch):
    calls = script(monkeypatch, [b"<html>ok</html>"])
    assert DummyScraper().fetch(URL) == "<html>ok</html>"
    assert len(calls) == 1


def test_retries_transient_error_then_succeeds(monkeypatch):
    calls = script(monkeypatch, [urllib.error.URLError("boom"), b"recovered"])
    assert DummyScraper().fetch(URL) == "recovered"
    assert len(calls) == 2


def test_no_retry_on_404(monkeypatch):
    """A dead URL is not a busy server — retrying it just burns budget."""
    calls = script(monkeypatch, [http_error(404)])
    with pytest.raises(RuntimeError, match="HTTP 404"):
        DummyScraper().fetch(URL)
    assert len(calls) == 1


def test_single_403_does_not_block_and_is_not_retried(monkeypatch):
    """One 403 really can be one dead URL — don't retry it, don't trip the breaker."""
    calls = script(monkeypatch, [http_error(403)])
    with pytest.raises(RuntimeError, match="HTTP 403") as exc:
        DummyScraper().fetch(URL)
    assert not isinstance(exc.value, HostBlocked)
    assert len(calls) == 1
    assert not blocked_hosts()


def test_consecutive_403s_trip_the_breaker(monkeypatch):
    """
    A run of 403s is a WAF refusing the client (Cloudflare vs Actions IPs), not a
    run of dead URLs. It must become HostBlocked so the orchestrator reports
    "could not scrape" and skips the freshness sweep — the 2026-07-31 techmatched
    case, where one lucky page turned a total block into a 15-product partial
    that swept 480 live rows.
    """
    scraper = DummyScraper()
    script(monkeypatch, [http_error(403)])

    for _ in range(base_scraper.BLOCK_AFTER_FORBIDDEN - 1):
        with pytest.raises(RuntimeError) as exc:
            scraper.fetch(URL)
        assert not isinstance(exc.value, HostBlocked)

    with pytest.raises(HostBlocked):
        scraper.fetch(URL)
    assert blocked_hosts() == {"example.test"}

    # Blocked host fails instantly, without touching the network.
    calls = script(monkeypatch, [b"would be fine"])
    with pytest.raises(HostBlocked):
        scraper.fetch(URL)
    assert not calls


def test_success_resets_the_403_counter(monkeypatch):
    """
    Isolated 403s across a long run must not accumulate into a block. This is
    exactly the techmatched shape: 403, then a page that succeeds, then more
    403s — the successful page proves the host is still serving us.
    """
    scraper = DummyScraper()
    for _ in range(base_scraper.BLOCK_AFTER_FORBIDDEN - 1):
        script(monkeypatch, [http_error(403)])
        with pytest.raises(RuntimeError):
            scraper.fetch(URL)

    script(monkeypatch, [b"ok"])
    assert scraper.fetch(URL) == "ok"

    script(monkeypatch, [http_error(403)])
    with pytest.raises(RuntimeError) as exc:
        scraper.fetch(URL)
    assert not isinstance(exc.value, HostBlocked)
    assert not blocked_hosts()


def test_403_breaker_is_per_host(monkeypatch):
    """One WAF-blocked retailer must not take down every other source in the run."""
    scraper = DummyScraper()
    script(monkeypatch, [http_error(403)])
    for _ in range(base_scraper.BLOCK_AFTER_FORBIDDEN - 1):
        with pytest.raises(RuntimeError):
            scraper.fetch(URL)
    with pytest.raises(HostBlocked):
        scraper.fetch(URL)

    assert blocked_hosts() == {"example.test"}
    script(monkeypatch, [b"fine"])
    assert scraper.fetch(OTHER) == "fine"


def test_404s_do_not_trip_the_403_breaker(monkeypatch):
    """
    End-of-pagination 404s are normal and unbounded (czone/amdhouse/rbt all do
    this) — only 403 counts toward the block.
    """
    scraper = DummyScraper()
    script(monkeypatch, [http_error(404)])
    for _ in range(base_scraper.BLOCK_AFTER_FORBIDDEN + 2):
        with pytest.raises(RuntimeError, match="HTTP 404"):
            scraper.fetch(URL)
    assert not blocked_hosts()


def test_429_retries_up_to_limit_then_raises_runtime_error(monkeypatch):
    calls = script(monkeypatch, [http_error(429)])
    with pytest.raises(RuntimeError) as exc:
        DummyScraper().fetch(URL, retries=4)
    assert not isinstance(exc.value, HostBlocked)  # first failed fetch only
    assert len(calls) == 4


def test_429_honours_retry_after(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(base_scraper.time, "sleep", lambda s: slept.append(s))
    script(monkeypatch, [http_error(429, {"Retry-After": "17"}), b"ok"])
    assert DummyScraper().fetch(URL) == "ok"
    assert slept and 17 <= slept[0] <= 19  # server's value plus jitter


def test_retry_after_is_capped(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(base_scraper.time, "sleep", lambda s: slept.append(s))
    script(monkeypatch, [http_error(429, {"Retry-After": "99999"}), b"ok"])
    DummyScraper().fetch(URL)
    assert slept[0] <= base_scraper.RETRY_AFTER_CAP + 2


def test_429_backoff_grows_when_no_retry_after(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(base_scraper.time, "sleep", lambda s: slept.append(s))
    script(monkeypatch, [http_error(429)])
    with pytest.raises(RuntimeError):
        DummyScraper().fetch(URL, retries=3)
    assert slept[0] >= base_scraper.BACKOFF_BASE
    assert slept[1] > slept[0]


def test_circuit_breaker_trips_after_repeated_429_fetches(monkeypatch):
    """Second fully-failed 429 fetch blocks the host for the rest of the run."""
    scraper = DummyScraper()
    script(monkeypatch, [http_error(429)])

    with pytest.raises(RuntimeError):
        scraper.fetch(URL, retries=2)
    assert not blocked_hosts()

    with pytest.raises(HostBlocked):
        scraper.fetch(URL, retries=2)
    assert blocked_hosts() == {"example.test"}


def test_blocked_host_short_circuits_without_network(monkeypatch):
    scraper = DummyScraper()
    script(monkeypatch, [http_error(429)])
    for _ in range(base_scraper.BLOCK_AFTER_FAILED_FETCHES):
        with pytest.raises(RuntimeError):
            scraper.fetch(URL, retries=2)

    calls = script(monkeypatch, [b"should never be requested"])
    with pytest.raises(HostBlocked):
        scraper.fetch(URL)
    assert calls == []


def test_breaker_is_per_host(monkeypatch):
    """One retailer blocking us must not stop the others."""
    scraper = DummyScraper()

    def fake_urlopen(req, timeout=None):
        if req.full_url == URL:
            raise http_error(429)
        return FakeResponse(b"fine")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    for _ in range(base_scraper.BLOCK_AFTER_FAILED_FETCHES):
        with pytest.raises(RuntimeError):
            scraper.fetch(URL, retries=2)

    assert blocked_hosts() == {"example.test"}
    assert scraper.fetch(OTHER) == "fine"


def test_success_resets_the_429_counter(monkeypatch):
    """Isolated 429s spread across a long run must not accumulate into a block."""
    scraper = DummyScraper()
    script(monkeypatch, [http_error(429)])
    with pytest.raises(RuntimeError):
        scraper.fetch(URL, retries=2)

    script(monkeypatch, [b"ok"])
    scraper.fetch(URL)

    script(monkeypatch, [http_error(429)])
    with pytest.raises(RuntimeError) as exc:
        scraper.fetch(URL, retries=2)
    assert not isinstance(exc.value, HostBlocked)
    assert not blocked_hosts()


def test_breaker_check_is_re_verified_inside_the_lock(monkeypatch):
    """
    G5: `if st["blocked"]: raise HostBlocked` used to run only BEFORE
    `st["lock"]` is acquired, and was never re-checked once inside it. A
    thread that queues on the lock while another thread trips the breaker
    would therefore go on to issue one more live request after acquiring the
    lock. That's a ban risk, not a cosmetic race, on the hosts
    (amdhouse/techmatched/zestro) the breaker exists for in the first place.

    Simulated deterministically: hold the host lock from the main thread (as
    if a first fetch() call were mid-request), start a second thread calling
    fetch() so it queues on that same lock, confirm it's actually blocked,
    then trip the breaker and release the lock exactly like the real
    with-block does. The queued thread must raise HostBlocked immediately on
    acquiring the lock, without calling urlopen.
    """
    import threading

    calls = script(monkeypatch, [b"<html>should never be reached</html>"])
    scraper = DummyScraper()
    host = base_scraper._host_of(URL)
    st = base_scraper._state(host)

    st["lock"].acquire()
    result: dict = {}

    def worker():
        try:
            scraper.fetch(URL)
        except HostBlocked as e:
            result["raised"] = e
        except Exception as e:  # pragma: no cover - would indicate a bug
            result["other"] = e

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=0.2)
    assert t.is_alive(), "worker must still be queued on the held lock"

    st["blocked"] = True
    st["lock"].release()

    t.join(timeout=2)
    assert not t.is_alive(), "worker did not finish after the lock was released"

    assert "raised" in result, result
    assert calls == [], "a thread queued behind a tripped breaker must not fetch"


def test_user_agent_is_not_browser_like():
    """
    The spoofed Chrome UA is what got amdhouse/zestro/techmatched 429ing every
    request; these hosts serve honest bot clients fine. Guard against a well-
    meaning "let's look more like a browser" regression.
    """
    ua = BaseScraper.USER_AGENT
    assert "Mozilla" not in ua
    assert "Chrome" not in ua
    assert "RigPK" in ua
