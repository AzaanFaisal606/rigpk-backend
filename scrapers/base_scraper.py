import json
import os
import time
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from datetime import datetime


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

    REQUEST_DELAY = 1.0  # seconds between requests; subclasses can override

    def fetch(self, url: str, retries: int = 3) -> str:
        """Fetch a URL and return the response text. Retries on failure."""
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
    def parse_price(price_text: str) -> int | None:
        """
        Parse a price string like 'Rs. 95,000' or 'PKR 1,20,000' into an int.
        Returns None if parsing fails.
        """
        import re
        digits = re.sub(r"[^\d]", "", price_text)
        return int(digits) if digits else None
