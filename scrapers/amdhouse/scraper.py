"""
amdhouse.pk scraper — WooCommerce / Flatsome theme

How it works:
  - SSR HTML: product titles in .woocommerce-loop-product__title > a
  - Prices in <bdi><span class="woocommerce-Price-currencySymbol">&#8360;</span>79,000</bdi>
  - Thumbnails in <img class="attachment-woocommerce_thumbnail ... src="...">
  - Pagination: ?paged=N  (20 products per page)

Usage:
    python -m scrapers.amdhouse.scraper
"""

import os
import re
import sys
import time

from scrapers.base_scraper import BaseScraper

SOURCE = "amdhouse.pk"
BASE = "https://amdhouse.pk"
PAGE_DELAY = 1.2

CATEGORIES: list[tuple[str, str]] = [
    ("graphics-cards", "gpu"),
    ("processors",     "cpu"),
    ("motherboards",   "motherboard"),
    ("ram",            "ram"),
    ("ssd",            "ssd"),
    ("hdd",            "hdd"),
    ("power-supply",   "psu"),
    ("casing",         "case"),
    ("cooling",        "cooling"),
    ("monitors",       "monitor"),
]


class AmdHouseScraper(BaseScraper):

    def scrape(self, url: str) -> list[dict]:
        all_products: list[dict] = []
        seen_urls: set[str] = set()
        page = 1

        while True:
            page_url = f"{url}?paged={page}" if page > 1 else url
            print(f"    page {page}: {page_url}")
            try:
                html = self.fetch(page_url)
            except Exception as e:
                print(f"    page {page} fetch failed ({e}) — stopping.")
                break
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

            page += 1
            time.sleep(PAGE_DELAY)

        return all_products

    def _parse_page(self, html: str) -> list[dict]:
        scraped_at = self._now()

        # Product blocks — split on product div class
        blocks = re.split(r'(?=<div[^>]+class="[^"]*product-small\s)', html)
        # First chunk is page header, skip it
        blocks = [b for b in blocks if 'woocommerce-loop-product__title' in b]

        if not blocks:
            return []

        results = []
        for block in blocks:
            # Name
            name_m = re.search(
                r'woocommerce-loop-product__title[^>]*><a[^>]+>([^<]+)', block
            )
            if not name_m:
                continue
            name = name_m.group(1).strip()

            # URL
            url_m = re.search(
                r'woocommerce-LoopProduct-link[^"]*"\s+href="([^"]+)"', block
            )
            # also try title anchor href
            if not url_m:
                url_m = re.search(r'href="(https://amdhouse\.pk/product/[^"]+)"', block)
            product_url = url_m.group(1) if url_m else ""

            # Price: <bdi><span>&#8360;</span>79,000</bdi>
            price_m = re.search(
                r'woocommerce-Price-amount[^>]*><bdi>.*?</span>([\d,]+)</bdi>', block, re.DOTALL
            )
            price_pkr = int(price_m.group(1).replace(",", "")) if price_m else None

            # Thumbnail
            thumb_m = re.search(
                r'<img[^>]+src="(https://amdhouse\.pk/wp-content/uploads/[^"]+)"', block
            )
            thumbnail = thumb_m.group(1) if thumb_m else None

            results.append({
                "name": name,
                "price_pkr": price_pkr,
                "url": product_url,
                "category": "",
                "source": SOURCE,
                "scraped_at": scraped_at,
                "thumbnail_url": thumbnail,
            })

        return results

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


def _find_valid_categories() -> list[tuple[str, str]]:
    """Filter CATEGORIES to only slugs that return products (404-safe)."""
    import urllib.request
    scraper = AmdHouseScraper()
    valid = []
    for slug, cat in CATEGORIES:
        url = f"{BASE}/product-category/{slug}/"
        try:
            html = scraper.fetch(url)
            if 'woocommerce-loop-product__title' in html:
                valid.append((url, cat))
                print(f"  [ok] {slug} -> {cat}")
            else:
                print(f"  [empty] {slug}")
        except Exception as e:
            print(f"  [skip] {slug}: {e}")
        time.sleep(0.5)
    return valid


def main():
    scraper = AmdHouseScraper()
    all_results: list[dict] = []

    print("Checking available categories...")
    valid = _find_valid_categories()

    for url, category in valid:
        print(f"\n[{category.upper()}] {url}")
        products = scraper.scrape(url)
        for p in products:
            p["category"] = category
        print(f"  => {len(products)} products")
        all_results.extend(products)

    if not all_results:
        print("No products scraped.")
        sys.exit(1)

    from collections import Counter
    counts = Counter(p["category"] for p in all_results)
    print(f"\nTotal: {len(all_results)} products")
    for cat, n in sorted(counts.items()):
        print(f"  {cat:15s} {n}")

    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from db.database import get_db
    db_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "ppc.db"))
    with get_db(db_path) as db:
        inserted = db.upsert_products(all_results)
        print(f"\nDB: {inserted} price rows written")
        s = db.stats()
        print(f"DB stats: {s['total_parts']} parts, {s['total_price_rows']} price rows")


if __name__ == "__main__":
    main()
