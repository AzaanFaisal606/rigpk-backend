"""
czone.com.pk GPU scraper

Scrapes GPU listings from:
  https://www.czone.com.pk/graphic-cards-pakistan-ppt.154.aspx

How it works:
  - czone.com.pk server-side renders (SSR) product data into the HTML.
  - Product names + URLs are in a <script type="application/ld+json"> block
    (schema.org CollectionPage > ItemList).
  - Prices are in <div class="product-price">Rs. XX,XXX</div> elements in the body.
  - Pagination uses ?page=N query param. We stop when a page returns 0 products.

No Playwright/browser needed — plain HTTP requests work.

Usage:
    python -m scrapers.czone.gpu_scraper
"""

import json
import os
import re
import sys
import time

from scrapers.base_scraper import BaseScraper

BASE_URL = "https://www.czone.com.pk/graphic-cards-pakistan-ppt.154.aspx"
CATEGORY = "gpu"
SOURCE = "czone.com.pk"
PAGE_DELAY = 1.0  # seconds between page requests


class CzoneGpuScraper(BaseScraper):

    def scrape(self, url: str) -> list[dict]:
        all_products = []
        seen_urls = set()
        page = 1

        while True:
            page_url = f"{url}?page={page}" if page > 1 else url
            print(f"Fetching page {page}: {page_url}")

            html = self.fetch(page_url)
            products = self._parse_page(html)

            if not products:
                print(f"  No products found on page {page}. Done.")
                break

            # Deduplicate — pages can overlap when total count isn't page-aligned
            new_products = [p for p in products if p["url"] not in seen_urls]
            for p in new_products:
                seen_urls.add(p["url"])

            print(f"  {len(new_products)} new products (page had {len(products)} total)")
            all_products.extend(new_products)

            if len(new_products) == 0:
                # All items on this page were already seen — we've looped back
                print("  All items already seen. Stopping.")
                break

            page += 1
            time.sleep(PAGE_DELAY)

        return all_products

    def _parse_page(self, html: str) -> list[dict]:
        scraped_at = self._now()

        # --- Names + URLs from JSON-LD ---
        items = self._extract_jsonld_items(html)
        if not items:
            return []

        # --- Prices from rendered HTML ---
        prices = re.findall(r'class="product-price">Rs\.\s*([\d,]+)', html)

        # Pair items with prices (they appear in the same order in the HTML)
        results = []
        for i, item in enumerate(items):
            name = item.get("name", "").strip()
            product_url = self._resolve_url(item.get("url", ""))

            price_str = prices[i] if i < len(prices) else None
            price_pkr = int(price_str.replace(",", "")) if price_str else None

            results.append({
                "name": name,
                "price_pkr": price_pkr,
                "url": product_url,
                "category": CATEGORY,
                "source": SOURCE,
                "scraped_at": scraped_at,
            })

        return results

    @staticmethod
    def _extract_jsonld_items(html: str) -> list[dict]:
        """Extract product list from schema.org CollectionPage JSON-LD."""
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
            return f"https://www.czone.com.pk{href}"
        return f"https://www.czone.com.pk/{href}"

    @staticmethod
    def _now() -> str:
        from datetime import timezone
        return __import__("datetime").datetime.now(timezone.utc).isoformat()


def main():
    scraper = CzoneGpuScraper()

    print("Starting czone.com.pk GPU scraper...")
    results = scraper.run(BASE_URL)

    if not results:
        print("No products scraped.")
        sys.exit(1)

    print(f"\nTotal unique products scraped: {len(results)}")

    print("\nSample results:")
    for item in results[:5]:
        price = f"Rs. {item['price_pkr']:,}" if item["price_pkr"] else "N/A"
        print(f"  {item['name'][:65]}")
        print(f"    Price: {price}")

    # Save to data/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.normpath(os.path.join(script_dir, "..", "..", "data"))
    output_path = os.path.join(output_dir, f"czone_gpus_{scraper.timestamp()}.json")
    scraper.save_to_json(results, output_path)


if __name__ == "__main__":
    main()
