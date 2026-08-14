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
import os
import re
import sys
import time

from scrapers.base_scraper import BaseScraper
from scrapers.exceptions import ScrapeIncomplete

SOURCE = "czone.com.pk"
BASE = "https://www.czone.com.pk"
PAGE_DELAY = 1.0  # seconds between page requests

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


class CzoneAllScraper(BaseScraper):

    MAX_PAGES = 200                  # czone's largest category is ~17 pages; 200 is pure insurance
    MAX_CONSECUTIVE_FAILURES = 3

    # Card boundary — each product lives in one of these divs. Name, URL and
    # price are all extracted from *within* one matched block below, never
    # from a document-wide pass, so a field missing on one card can never
    # shift onto the next one.
    _CARD_START_RE = re.compile(r'<div class="product-card-2[^"]*"')
    _NAME_RE = re.compile(r'alt="([^"]+)"')
    _URL_RE = re.compile(r'href="([^"]+)"')
    _PRICE_RE = re.compile(r'class="product-price">Rs\.\s*([\d,]+)')
    _THUMB_RE = re.compile(r'<img[^>]+src="([^"]+)"')

    def scrape(self, url: str) -> list[dict]:
        """Scrape all pages for a single category URL.

        czone uses a sliding-window pagination: every page after the first
        drops the oldest item and adds one new item at the end. So page 1
        gives us 12 products, then each subsequent page yields exactly 1 new
        product. We stop once collected == total (extracted from page 1 HTML),
        when a page returns 0 products / all-seen items, when MAX_PAGES is hit,
        or after MAX_CONSECUTIVE_FAILURES fetch failures in a row — a
        persistent failure must end the run, not spin until the job timeout.
        """
        all_products: list[dict] = []
        seen_urls: set[str] = set()
        total: int | None = None
        page = 1
        failures = 0

        while page <= self.MAX_PAGES:
            page_url = f"{url}?page={page}" if page > 1 else url
            print(f"    page {page}: {page_url}")

            try:
                page_html = self.fetch(page_url)
                failures = 0
            except Exception as e:
                failures += 1
                print(f"    SKIP page {page} ({e}) — failure {failures}/{self.MAX_CONSECUTIVE_FAILURES}")
                if failures >= self.MAX_CONSECUTIVE_FAILURES:
                    exc = ScrapeIncomplete(
                        f"czone: {failures} consecutive page failures at page {page}"
                    )
                    exc.partial_results = all_products
                    raise exc from e
                page += 1
                time.sleep(PAGE_DELAY)
                continue

            # Extract total product count from first page
            if page == 1:
                total = self._extract_total(page_html)
                if total is not None:
                    print(f"    total products reported: {total}")

            products = self._parse_page(page_html)

            if not products:
                print(f"    no products on page {page} — done.")
                break

            new = [p for p in products if p["url"] not in seen_urls]
            for p in new:
                seen_urls.add(p["url"])

            print(f"    {len(new)} new (page total: {len(products)}, collected: {len(all_products) + len(new)})")
            all_products.extend(new)

            if not new:
                break

            # Stop early if we've collected everything
            if total is not None and len(all_products) >= total:
                print(f"    collected all {total} products — done.")
                break

            page += 1
            time.sleep(PAGE_DELAY)
        else:
            # A page cap this generous getting hit at all means the site is
            # serving "new" content forever (or is broken) — trust nothing
            # collected so far rather than deliver a harvest of unknown shape.
            raise ScrapeIncomplete(f"czone: hit MAX_PAGES={self.MAX_PAGES} without finishing")

        return all_products

    def _parse_page(self, html_text: str) -> list[dict]:
        """
        Parse one listing page, card by card.

        Each product's name, price and URL come from the same block. An
        earlier version parsed three flat sequences (JSON-LD items, a
        document-wide price regex, an out-of-stock flag list) and zipped them
        by index, so a card missing a price silently gave every following
        product its neighbour's price — well-formed output with wrong data,
        undetectable downstream.
        """
        scraped_at = self.now()
        results = []
        for block in self._split_cards(html_text):
            if "Out Of Stock" in block:
                continue          # sold out — no price to report, skip entirely

            name = self._extract_name(block)
            url = self._extract_url(block)
            if not name or not url:
                continue          # a card we cannot identify is skipped, not guessed

            results.append({
                "name": name,
                "price_pkr": self._extract_price(block),   # None = hidden/missing on this card
                "url": self._resolve_url(url),
                "category": "",   # filled in by caller
                "source": SOURCE,
                "scraped_at": scraped_at,
                "thumbnail_url": self._extract_thumb(block),
            })

        return results

    @classmethod
    def _split_cards(cls, html_text: str) -> list[str]:
        """Split a listing page into one block per product card."""
        starts = [m.start() for m in cls._CARD_START_RE.finditer(html_text)]
        if not starts:
            return []
        starts.append(len(html_text))
        return [html_text[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]

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
    import sys
    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from db.database import get_db

    scraper = CzoneAllScraper()
    all_results: list[dict] = []

    for path, category in CATEGORIES:
        url = f"{BASE}{path}"
        print(f"\n[{category.upper()}] {url}")
        products = scraper.scrape(url)
        for p in products:
            p["category"] = category
        print(f"  => {len(products)} products")
        all_results.extend(products)

    if not all_results:
        print("No products scraped.")
        sys.exit(1)

    print(f"\nTotal products scraped across all categories: {len(all_results)}")

    from collections import Counter
    counts = Counter(p["category"] for p in all_results)
    for cat, count in sorted(counts.items()):
        print(f"  {cat:15s} {count}")

    # Write to database
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.normpath(os.path.join(script_dir, "..", "..", "data", "ppc.db"))
    with get_db(db_path) as db:
        inserted = db.upsert_products(all_results)
        print(f"\nDB: {inserted} price rows written to {db_path}")
        stats = db.stats()
        print(f"DB stats: {stats['total_parts']} total parts, {stats['total_price_rows']} price rows")


if __name__ == "__main__":
    main()
