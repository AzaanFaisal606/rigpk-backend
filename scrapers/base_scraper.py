import json
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone


class HostBlocked(RuntimeError):
    """
    Raised when a host has refused enough requests in a row that continuing to
    hit it is pointless (and counterproductive). Once a host is in this state
    every further fetch() to it fails instantly without touching the network.

    Orchestrators treat this as "the source could not be scraped at all" — the
    freshness sweep is skipped and the retailer keeps its existing rows.
    """


# ----------------------------------------------------------------------
# Per-host request state (pacing + circuit breaker)
#
# Keyed on netloc, process-global: two scrapers pointed at the same domain
# share one budget. Reset between runs with reset_host_state().
# ----------------------------------------------------------------------

BLOCK_AFTER_FAILED_FETCHES = 2   # consecutive fully-failed 429 fetches -> blocked
BLOCK_AFTER_FORBIDDEN = 3        # consecutive 403s on one host -> blocked
BACKOFF_BASE = 8.0               # seconds; first 429 retry waits ~this long
BACKOFF_CAP = 120.0
RETRY_AFTER_CAP = 300.0          # ignore absurd Retry-After values

_host_state: dict[str, dict] = {}

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
    return _host_state.setdefault(
        host, {"last_request": 0.0, "fail_429": 0, "fail_403": 0, "blocked": False}
    )


def host_blocked(host: str) -> bool:
    """True if `host` tripped the circuit breaker earlier in this process."""
    return _state(host.lower())["blocked"]


def blocked_hosts() -> set[str]:
    """Every host that tripped the circuit breaker this process."""
    return {h for h, s in _host_state.items() if s["blocked"]}


def reset_host_state(host: str | None = None) -> None:
    """Clear breaker/pacing state — for tests, or to retry a host deliberately."""
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
    TIMEOUT = 45

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

    def fetch(self, url: str, retries: int = 4) -> str:
        """
        Fetch a URL and return the response text.

        Retry policy is per failure kind:
          * 429            — long exponential backoff with jitter, honouring
                             Retry-After when present. Enough of these in a row
                             trips the host's circuit breaker.
          * 4xx in NO_RETRY_CODES — no retry; the URL is wrong, not busy.
          * everything else (5xx, timeouts, DNS) — short backoff, retry.

        Raises HostBlocked if the host is already blocked or becomes blocked,
        RuntimeError for any other exhausted failure.
        """
        host = _host_of(url)
        st = _state(host)
        if st["blocked"]:
            raise HostBlocked(f"{host} is blocked for this run — skipped {url}")

        last_err: Exception | None = None
        for attempt in range(retries):
            self._pace(host)
            try:
                req = urllib.request.Request(url, headers=self.HEADERS)
                with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                    body = resp.read().decode("utf-8", errors="ignore")
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
