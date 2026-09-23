"""
Base class for prebuilt PC scrapers.

Output schema per item:
    {
        "name":          str,
        "price_pkr":     int | None,
        "url":           str,
        "source":        str,
        "scraped_at":    str,         # ISO 8601 UTC
        "thumbnail_url": str | None,
        "components":    dict | None, # {"cpu": ..., "gpu": ..., "ram": ..., ...}
    }
"""

from __future__ import annotations

import re
from abc import abstractmethod
from pathlib import Path

from scrapers.base_scraper import BaseScraper


_AVAILABILITY_RE = re.compile(r'"availability"\s*:\s*"(?:https?://schema\.org/)?(\w+)"')
_SOLD_OUT = {"OutOfStock", "SoldOut", "Discontinued"}


def is_sold_out(html: str) -> bool:
    """A product page's JSON-LD availability; the Woo stock badge if it has none.
    Related-product cards on the page carry their own `outofstock` classes,
    so class tokens alone can't be trusted."""
    avail = _AVAILABILITY_RE.findall(html)
    if avail:
        return all(a in _SOLD_OUT for a in avail)
    return bool(re.search(r'<p[^>]+class="[^"]*\bstock\b[^"]*\bout-of-stock\b', html))


class BasePrebuiltScraper(BaseScraper):

    PAGE_DELAY = 1.2  # prebuilt scrapers need slower rate

    @abstractmethod
    def scrape_all(self) -> list[dict]:
        """Scrape all prebuilt listings and return list of prebuilt dicts."""
        ...

    # BaseScraper provides: fetch(), now(), parse_price(), USER_AGENT, HEADERS, REQUEST_DELAY

    @staticmethod
    def write_to_db(prebuilts: list[dict], db_path: str | None = None) -> int:
        root = Path(__file__).parent.parent.parent
        path = db_path or str(root / "data" / "ppc.db")
        from db.database import get_db
        # Rows only. run_prebuilts.py owns the schema and has already opened
        # with allow_remote_migrations=True by the time this runs under it.
        with get_db(path, allow_remote_migrations=False) as db:
            n = db.upsert_prebuilts(prebuilts)
            stats = db.prebuilt_stats()
        print(f"DB: {n} prebuilts upserted — total {stats['total']} across {stats['by_source']}")
        return n

    # scrape() is abstract in BaseScraper — prebuilt scrapers don't use it directly.
    # Implement it to satisfy the ABC, delegating to scrape_all().
    def scrape(self, url: str) -> list[dict]:
        return self.scrape_all()
