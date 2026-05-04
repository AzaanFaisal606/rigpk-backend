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

import json
import os
import re
import sys
import time

from scrapers.base_scraper import BaseScraper

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

    def scrape(self, url: str) -> list[dict]:
        """Scrape all pages for a single category URL.

        czone uses a sliding-window pagination: every page after the first
        drops the oldest item and adds one new item at the end. So page 1
        gives us 12 products, then each subsequent page yields exactly 1 new
        product. We stop once collected == total (extracted from page 1 HTML)
        or when a page returns 0 products / all-seen items.
        """
        all_products = []
        seen_urls: set[str] = set()
        total: int | None = None
        page = 1

        while True:
            page_url = f"{url}?page={page}" if page > 1 else url
            print(f"    page {page}: {page_url}")

            try:
                html = self.fetch(page_url)
            except RuntimeError as e:
                print(f"    SKIP page {page} ({e}) — continuing")
                page += 1
                time.sleep(PAGE_DELAY)
                continue

            # Extract total product count from first page
            if page == 1:
                total = self._extract_total(html)
                if total is not None:
                    print(f"    total products reported: {total}")

            products = self._parse_page(html)

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

        return all_products

    def _parse_page(self, html: str) -> list[dict]:
        scraped_at = self.now()
        items = self._extract_jsonld_items(html)
        if not items:
            return []

        prices = re.findall(r'class="product-price">Rs\.\s*([\d,]+)', html)

        results = []
        for i, item in enumerate(items):
            name = item.get("name", "").strip()
            product_url = self._resolve_url(item.get("url", ""))
            price_str = prices[i] if i < len(prices) else None
            price_pkr = int(price_str.replace(",", "")) if price_str else None
            thumbnail = item.get("image") or None

            results.append({
                "name": name,
                "price_pkr": price_pkr,
                "url": product_url,
                "category": "",   # filled in by caller
                "source": SOURCE,
                "scraped_at": scraped_at,
                "thumbnail_url": thumbnail,
            })

        return results

    @staticmethod
    def _extract_total(html: str) -> int | None:
        """Extract the total product count shown on the listing page."""
        # czone renders e.g. "32 Products" near the top of the listing
        m = re.search(r'(\d+)\s+Products?', html, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _extract_jsonld_items(html: str) -> list[dict]:
        blocks = re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        for block in blocks:
            try:
                data = json.loads(block)
                if data.get("@type") == "CollectionPage":
                    return data.get("mainEntity", {}).get("itemListElement", [])
            except json.JSONDecodeError:
                continue
        return []

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
