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

from abc import abstractmethod
from pathlib import Path

from scrapers.base_scraper import BaseScraper


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
        with get_db(path) as db:
            n = db.upsert_prebuilts(prebuilts)
            stats = db.prebuilt_stats()
        print(f"DB: {n} prebuilts upserted — total {stats['total']} across {stats['by_source']}")
        return n

    # scrape() is abstract in BaseScraper — prebuilt scrapers don't use it directly.
    # Implement it to satisfy the ABC, delegating to scrape_all().
    def scrape(self, url: str) -> list[dict]:
        return self.scrape_all()
