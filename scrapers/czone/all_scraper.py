"""
czone.com.pk — all gaming PC parts scraper

Scrapes the following categories from czone.com.pk:
  - GPU (Graphic Cards)
  - CPU (Processors)
  - Motherboard
  - RAM (Memory Module)
  - SSD (Solid-State Drives)
  - HDD (Hard Drives)
  - PSU (Power Supply)
  - Case (Casing)
  - Cooling (Cooling Solutions)
  - Monitor (LCD/LED Monitors)

How it works:
  - czone.com.pk SSR-renders product data into the HTML.
  - Product names + URLs come from <script type="application/ld+json"> (schema.org ItemList).
  - Prices come from <div class="product-price">Rs. XX,XXX</div> in the body.
  - Pagination uses ?page=N. We stop when a page returns 0 products.

No Playwright needed — plain HTTP requests work.

Usage:
    python -m scrapers.czone.all_scraper
"""

import html
import re
import time  # kept for tests that monkeypatch this module's `time.sleep`

from scrapers.listing_scraper import ListingScraper, run_listing_cli

SOURCE = "czone.com.pk"
BASE = "https://www.czone.com.pk"

# Category slug → (display name, category key used in output JSON)
CATEGORIES: list[tuple[str, str]] = [
    ("/graphic-cards-pakistan-ppt.154.aspx",          "gpu"),
    ("/processors-pakistan-ppt.85.aspx",              "cpu"),
    ("/motherboards-pakistan-ppt.157.aspx",           "motherboard"),
    ("/memory-module-ram-pakistan-ppt.127.aspx",      "ram"),
    ("/solid-state-drives-ssd-pakistan-ppt.263.aspx", "ssd"),
    ("/hard-drives-pakistan-ppt.93.aspx",             "hdd"),
    ("/power-supply-pakistan-ppt.183.aspx",           "psu"),
    ("/casing-pakistan-ppt.168.aspx",                 "case"),
    ("/cooling-solutions-pakistan-ppt.141.aspx",      "cooling"),
    ("/lcd-led-monitors-pakistan-ppt.108.aspx",       "monitor"),
]


class CzoneAllScraper(ListingScraper):

    SOURCE = SOURCE
    MAX_PAGES = 200                  # czone's largest category is ~17 pages; 200 is pure insurance

    # Card boundary — each product lives in one of these divs. Name, URL and
    # price are all extracted from *within* one matched block below, never
    # from a document-wide pass, so a field missing on one card can never
    # shift onto the next one.
    _CARD_START_RE = re.compile(r'<div class="product-card-2[^"]*"')
    _NAME_RE = re.compile(r'alt="([^"]+)"')
    _URL_RE = re.compile(r'href="([^"]+)"')
    _PRICE_RE = re.compile(r'class="product-price">Rs\.\s*([\d,]+)')
    _THUMB_RE = re.compile(r'<img[^>]+src="([^"]+)"')

    def card_blocks(self, html_text: str) -> list[str]:
        """Split a listing page into one block per product card."""
        starts = [m.start() for m in self._CARD_START_RE.finditer(html_text)]
        if not starts:
            return []
        starts.append(len(html_text))
        return [html_text[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]

    def parse_card(self, block: str) -> dict | None:
        """
        One product's name, price and URL come from the same block. An
        earlier version parsed three flat sequences (JSON-LD items, a
        document-wide price regex, an out-of-stock flag list) and zipped
        them by index, so a card missing a price silently gave every
        following product its neighbour's price — well-formed output with
        wrong data, undetectable downstream.
        """
        if "Out Of Stock" in block:
            return None          # sold out — no price to report, skip entirely

        name = self._extract_name(block)
        url = self._extract_url(block)
        if not name or not url:
            return None          # a card we cannot identify is skipped, not guessed

        return {
            "name": name,
            "price_pkr": self._extract_price(block),   # None = hidden/missing on this card
            "url": self._resolve_url(url),
            "category": "",   # filled in by caller
            "source": SOURCE,
            "scraped_at": self.now(),
            "thumbnail_url": self._extract_thumb(block),
        }

    def extract_total(self, html_text: str) -> int | None:
        return self._extract_total(html_text)

    @classmethod
    def _extract_name(cls, block: str) -> str:
        m = cls._NAME_RE.search(block)
        return html.unescape(m.group(1)).strip() if m else ""

    @classmethod
    def _extract_url(cls, block: str) -> str:
        m = cls._URL_RE.search(block)
        return m.group(1) if m else ""

    @classmethod
    def _extract_price(cls, block: str) -> int | None:
        m = cls._PRICE_RE.search(block)
        return int(m.group(1).replace(",", "")) if m else None

    @classmethod
    def _extract_thumb(cls, block: str) -> str | None:
        m = cls._THUMB_RE.search(block)
        return m.group(1) if m else None

    @staticmethod
    def _extract_total(html_text: str) -> int | None:
        """Extract the total product count shown on the listing page."""
        # czone renders e.g. "32 Products" near the top of the listing
        m = re.search(r'(\d+)\s+Products?', html_text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _resolve_url(href: str) -> str:
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return f"{BASE}{href}"
        return f"{BASE}/{href}"


def main():
    run_listing_cli(CzoneAllScraper, CATEGORIES, lambda path: f"{BASE}{path}", write_db=True)


if __name__ == "__main__":
    main()
