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

import os
import sys
import time
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from datetime import datetime, timezone


class BasePrebuiltScraper(ABC):

    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    HEADERS = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    PAGE_DELAY = 1.2

    def fetch(self, url: str, retries: int = 3) -> str:
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers=self.HEADERS)
                with urllib.request.urlopen(req, timeout=45) as resp:
                    return resp.read().decode("utf-8", errors="ignore")
            except urllib.error.URLError as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts: {e}") from e
        return ""

    @abstractmethod
    def scrape_all(self) -> list[dict]:
        """Scrape all prebuilt listings and return list of prebuilt dicts."""
        ...

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def write_to_db(prebuilts: list[dict], db_path: str | None = None) -> int:
        root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
        sys.path.insert(0, root)
        from db.database import get_db
        path = db_path or os.path.join(root, "data", "ppc.db")
        with get_db(path) as db:
            n = db.upsert_prebuilts(prebuilts)
            stats = db.prebuilt_stats()
        print(f"DB: {n} prebuilts upserted — total {stats['total']} across {stats['by_source']}")
        return n
