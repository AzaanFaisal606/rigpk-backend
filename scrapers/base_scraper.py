import json
import os
import random
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from scrapers.exceptions import ScrapeIncomplete


class HostBlocked(ScrapeIncomplete, RuntimeError):
    """
    Raised when a host has refused enough requests in a row that continuing to
    hit it is pointless (and counterproductive). Once a host is in this state
    every further fetch() to it fails instantly without touching the network.

    Orchestrators treat this as "the source could not be scraped at all" — the
    freshness sweep is skipped and the retailer keeps its existing rows.

    Subclasses ScrapeIncomplete (not just RuntimeError) so it propagates as one
    everywhere a caller catches ScrapeIncomplete specifically — a host block
    mid-run must never be read as "an ordinary bad page" by a consecutive-
    failure counter and quietly folded into a generic error.
    """


def is_http_404(exc: BaseException) -> bool:
    """
    True if `exc` is (or was caused by) an HTTP 404.

    BaseScraper.fetch() puts 404 in NO_RETRY_CODES and re-raises it as
    `RuntimeError(...) from e`, so the HTTPError normally shows up as
    `exc.__cause__`, not as `exc` itself. Check both so this also works
    against a bare HTTPError (e.g. from a test double). Shared so every
    scraper that needs to tell "page retired" from "something is actually
    wrong" uses the same check instead of a lookalike of its own.
    """
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        return True
    cause = exc.__cause__
    return isinstance(cause, urllib.error.HTTPError) and cause.code == 404


# ----------------------------------------------------------------------
# Per-host request state (pacing + circuit breaker)
#
# Keyed on netloc, process-global: two scrapers pointed at the same domain
# share one budget. Reset between runs with reset_host_state().
#
# run_all.py now runs one worker thread per source. Two locks make that safe:
#
# 1. _host_state_lock guards the top-level dict itself. One thread inserting
#    a new host key (_state()'s setdefault, on a host's first fetch()) while
#    another thread iterates the whole dict (blocked_hosts(), or
#    reset_host_state()'s clear()) is a real race — CPython dict iteration is
#    not atomic across the whole loop, so a concurrent insert during that
#    loop raises "dictionary changed size during iteration".
#
# 2. Each host's own state dict carries a "lock" (st["lock"]) that fetch()
#    holds for its entire retry loop — pacing decision, the request itself,
#    and the fail_429/fail_403/blocked updates. In this codebase every
#    source maps to exactly one host, so in practice only one thread ever
#    calls fetch() for a given host anyway — but that's a property of the
#    SCRAPERS registry, not something the code enforced. st["lock"] makes
#    "never two concurrent requests to one host" a guarantee fetch() itself
#    holds, not just an accident of how sources happen to be assigned.
#    It's per-host, not the module-wide _host_state_lock, so a slow/retrying
#    request to one host never blocks a different host's thread.
# ----------------------------------------------------------------------

BLOCK_AFTER_FAILED_FETCHES = 2   # consecutive fully-failed 429 fetches -> blocked
BLOCK_AFTER_FORBIDDEN = 3        # consecutive 403s on one host -> blocked
BACKOFF_BASE = 8.0               # seconds; first 429 retry waits ~this long
BACKOFF_CAP = 120.0
RETRY_AFTER_CAP = 300.0          # ignore absurd Retry-After values

_host_state: dict[str, dict] = {}
_host_state_lock = threading.Lock()

# Status codes that mean "this URL is wrong", not "slow down". Retrying them
# wastes the request budget and, on Hostinger/LiteSpeed hosts, a retired
# category can answer 429 to browser-like clients — see docs/scraper-solutions.md #4b.
#
# 403 is the ambiguous one: a single 403 really can mean one dead URL, but a
# *run* of them means a WAF is refusing this client (Cloudflare does this to
# GitHub Actions egress IPs). So 403 stays un-retried, but consecutive 403s
# feed the circuit breaker — see BLOCK_AFTER_FORBIDDEN in fetch().
NO_RETRY_CODES = frozenset({400, 401, 403, 404, 410, 451})


def _host_of(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def _state(host: str) -> dict:
    with _host_state_lock:
        return _host_state.setdefault(
            host, {"last_request": 0.0, "fail_429": 0, "fail_403": 0,
                   "blocked": False, "lock": threading.Lock()}
        )


def host_blocked(host: str) -> bool:
    """True if `host` tripped the circuit breaker earlier in this process."""
    return _state(host.lower())["blocked"]


def blocked_hosts() -> set[str]:
    """Every host that tripped the circuit breaker this process."""
    with _host_state_lock:
        return {h for h, s in _host_state.items() if s["blocked"]}


def reset_host_state(host: str | None = None) -> None:
    """Clear breaker/pacing state — for tests, or to retry a host deliberately."""
    with _host_state_lock:
        if host is None:
            _host_state.clear()
        else:
            _host_state.pop(host.lower(), None)


def scraped_at_now() -> str:
    """
    UTC ISO-8601 timestamp for a product's `scraped_at`.

    If the env var SCRAPE_AS_OF_DATE (YYYY-MM-DD) is set, its date replaces the
    date part while the current time-of-day is kept. The heal rerun sets it to
    the most recent scrape date so a fix merged on a later calendar day still
    lands in the weekly scrape's trend bucket (buckets are the UTC date prefix of
    scraped_at) instead of creating a stray one-source bucket. A malformed value
    is ignored so a bad env can never corrupt the timestamp.
    """
    ts = datetime.now(timezone.utc)
    override = os.getenv("SCRAPE_AS_OF_DATE")
    if override:
        try:
            datetime.strptime(override, "%Y-%m-%d")
        except ValueError:
            override = None
    if override:
        return f"{override}T{ts.strftime('%H:%M:%S.%f')}+00:00"
    return ts.isoformat()


class BaseScraper(ABC):
    """
    Abstract base class for all PPC scrapers.

    Subclasses must implement:
        scrape(url) -> list[dict]

    Each dict in the returned list should conform to the standard product schema:
        {
            "name": str,
            "price_pkr": int | None,
            "url": str,
            "category": str,
            "source": str,
            "scraped_at": str,  # ISO 8601
            "thumbnail_url": str | None
        }
    """

    # Identify honestly. Do NOT put a browser User-Agent here.
    #
    # Several of the retailers (amdhouse, zestrogaming, techmatched — all
    # Hostinger/LiteSpeed WordPress) run a bot challenge that fires on clients
    # *claiming* to be a browser but not solving the JS/cookie challenge. Those
    # get a blanket 429; a plain non-browser UA is served normally. Verified
    # 2026-07-21 by alternating UAs against one URL in the same second:
    # Chrome UA -> 429, this UA -> 200, repeatably. See docs/scraper-solutions.md.
    USER_AGENT = "RigPK-PriceBot/1.0 (+https://github.com/AzaanFaisal606/rigpk-backend)"

    HEADERS = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    REQUEST_DELAY = 1.0  # min seconds between requests to one host; jittered
    TIMEOUT = 45  # kept for back-compat; fetch() itself now uses CONNECT_TIMEOUT

    # timeout= on urlopen() bounds connect and each individual socket read, but
    # a keep-alive connection that trickles bytes forever (zestro's failure
    # mode, H8) can outlive it in practice — no single read ever times out.
    # READ_DEADLINE is a wall-clock cap across the *whole* body read, checked
    # between chunks in fetch() below. Both live here, not per-scraper, so
    # every subclass inherits the same bound.
    CONNECT_TIMEOUT = 45
    READ_DEADLINE = 90

    def _pace(self, host: str) -> None:
        """
        Space requests to a host by REQUEST_DELAY, jittered. Jitter matters:
        a fixed interval is itself a bot signature, and it keeps concurrent
        scrapers of the same host from lock-stepping.
        """
        st = _state(host)
        wait = self.REQUEST_DELAY * random.uniform(0.8, 1.6)
        elapsed = time.monotonic() - st["last_request"]
        if st["last_request"] and elapsed < wait:
            time.sleep(wait - elapsed)
        st["last_request"] = time.monotonic()

    @staticmethod
    def _retry_after(err: urllib.error.HTTPError) -> float | None:
        """Seconds from a Retry-After header, if the server sent a usable one."""
        raw = err.headers.get("Retry-After") if err.headers else None
        if not raw:
            return None
        try:
            return min(float(raw.strip()), RETRY_AFTER_CAP)
        except ValueError:
            return None  # HTTP-date form; rare here, not worth parsing

    def fetch(
        self,
        url: str,
        retries: int = 4,
        headers: dict | None = None,
        data: bytes | None = None,
    ) -> str:
        """
        Fetch a URL and return the response text.

        Retry policy is per failure kind:
          * 429            — long exponential backoff with jitter, honouring
                             Retry-After when present. Enough of these in a row
                             trips the host's circuit breaker.
          * 4xx in NO_RETRY_CODES — no retry; the URL is wrong, not busy.
          * everything else (5xx, timeouts, DNS) — short backoff, retry.

        `headers` are merged on top of the default HEADERS (letting a caller
        override e.g. Accept, or add Authorization) without losing the honest
        bot User-Agent. `data` is passed straight to urllib.request.Request —
        non-None turns the request into a POST, same as urllib's own rule.
        Both default to None so every existing caller is unaffected.

        Raises HostBlocked if the host is already blocked or becomes blocked,
        RuntimeError for any other exhausted failure.
        """
        host = _host_of(url)
        st = _state(host)
        if st["blocked"]:
            raise HostBlocked(f"{host} is blocked for this run — skipped {url}")

        req_headers = {**self.HEADERS, **headers} if headers else self.HEADERS

        # Held for the whole retry loop below: pacing, the request itself,
        # and every counter update. This is what actually enforces "one
        # request to this host at a time" when fetch() is called from
        # several threads — see the per-host lock note above _host_state.
        with st["lock"]:
            # Re-check: the check above the lock can pass, then this thread
            # queues on st["lock"] while another thread (holding it) trips
            # the breaker and releases. Without re-checking here, the queued
            # thread would go on to issue one more live request past the
            # trip — a ban risk on hosts (amdhouse/techmatched/zestro) that
            # ban bots, not just a cosmetic race.
            if st["blocked"]:
                raise HostBlocked(f"{host} is blocked for this run — skipped {url}")
            last_err: Exception | None = None
            for attempt in range(retries):
                self._pace(host)
                try:
                    req = urllib.request.Request(url, data=data, headers=req_headers)
                    # timeout= covers connect and each individual socket read, not
                    # a body that trickles forever — a hard wall-clock deadline
                    # across the whole read is the only thing that bounds that.
                    with urllib.request.urlopen(req, timeout=self.CONNECT_TIMEOUT) as resp:
                        deadline = time.monotonic() + self.READ_DEADLINE
                        chunks = []
                        while (chunk := resp.read(65536)):
                            chunks.append(chunk)
                            if time.monotonic() > deadline:
                                raise TimeoutError(
                                    f"read of {url} exceeded READ_DEADLINE={self.READ_DEADLINE}s"
                                )
                        body = b"".join(chunks).decode("utf-8", errors="ignore")
                    st["fail_429"] = 0
                    st["fail_403"] = 0
                    return body

                except urllib.error.HTTPError as e:
                    last_err = e
                    if e.code in NO_RETRY_CODES:
                        # A run of 403s is a WAF refusing this client, not a run of
                        # dead URLs. Trip the breaker so the caller reports "could
                        # not scrape" instead of a misleading partial harvest that
                        # would sweep every unseen row. Any 2xx resets the count.
                        if e.code == 403:
                            st["fail_403"] += 1
                            if st["fail_403"] >= BLOCK_AFTER_FORBIDDEN:
                                st["blocked"] = True
                                raise HostBlocked(
                                    f"{host} returned 403 on {st['fail_403']} consecutive fetches "
                                    f"— treating as a block, not as dead URLs"
                                ) from e
                        raise RuntimeError(f"Failed to fetch {url}: HTTP {e.code}") from e
                    if e.code == 429:
                        if attempt < retries - 1:
                            delay = self._retry_after(e)
                            if delay is None:
                                delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** attempt))
                            time.sleep(delay + random.uniform(0, 2))
                            continue
                        st["fail_429"] += 1
                        if st["fail_429"] >= BLOCK_AFTER_FAILED_FETCHES:
                            st["blocked"] = True
                            raise HostBlocked(
                                f"{host} returned 429 on {st['fail_429']} consecutive fetches "
                                f"— giving up on this host for the rest of the run"
                            ) from e
                    elif attempt < retries - 1:
                        time.sleep(2 ** attempt + random.uniform(0, 1))
                        continue

                except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
                    last_err = e
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt + random.uniform(0, 1))
                        continue

            raise RuntimeError(f"Failed to fetch {url} after {retries} attempts: {last_err}")

    @abstractmethod
    def scrape(self, url: str) -> list[dict]:
        """Scrape the given URL and return a list of product dicts."""
        ...

    def run(self, url: str) -> list[dict]:
        """Run the scraper and return results."""
        return self.scrape(url)

    @staticmethod
    def save_to_json(data: list[dict], filepath: str):
        """Save scraped data to a JSON file. Creates parent dirs if needed."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(data)} items to {filepath}")

    @staticmethod
    def timestamp() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def now() -> str:
        return scraped_at_now()

    @staticmethod
    def parse_price(price_text: str) -> int | None:
        """
        Parse a price string like 'Rs. 95,000' or 'PKR 1,20,000' into an int.
        Returns None if parsing fails.
        """
        import re
        digits = re.sub(r"[^\d]", "", price_text)
        return int(digits) if digits else None
