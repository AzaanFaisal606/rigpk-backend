"""
rbtechngames.com scraper — WooCommerce / Flatsome theme

How it works:
  - SSR HTML: product titles in .woocommerce-loop-product__title > a
  - Prices in .woocommerce-Price-amount > bdi
  - Thumbnails in <img> within the product box (srcset, pick largest or first)
  - Pagination: ?paged=N on the product-category URL

Usage:
    python -m scrapers.rbtechngames.scraper
"""

import os
import re
import sys
import time

from scrapers.base_scraper import BaseScraper

SOURCE = "rbtechngames.com"
BASE = "https://rbtechngames.com"
PAGE_DELAY = 1.2

# rbt uses /product-category/ URL structure
CATEGORIES: list[tuple[str, str]] = [
    ("computers/graphics-card",  "gpu"),
    ("computers/processors",     "cpu"),
    ("computers/motherboards",   "motherboard"),
    ("computers/rams",           "ram"),
    ("computers/storage",        "ssd"),   # covers SSD + HDD
    ("computers/power-supplies", "psu"),
    ("computers/casings",        "case"),
    ("computers/cpu-coolers",    "cooling"),
    ("computers/monitors",       "monitor"),
]


class RbTechNGamesScraper(BaseScraper):

    def scrape(self, url: str) -> list[dict]:
        all_products: list[dict] = []
        seen_urls: set[str] = set()
        page = 1

        while True:
            page_url = f"{url}/page/{page}/" if page > 1 else url
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

        # Split into product blocks
        blocks = re.split(r'(?=<div[^>]+class="[^"]*product-small\s)', html)
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
            # Decode HTML entities
            name = name.replace("&#8211;", "–").replace("&amp;", "&").replace("&#039;", "'")

            # URL
            url_m = re.search(r'href="(https://rbtechngames\.com/[^"]+)"[^>]*class="woocommerce-LoopProduct', block)
            if not url_m:
                url_m = re.search(r'href="(https://rbtechngames\.com/shop[^"]+)"', block)
            if not url_m:
                url_m = re.search(r'href="(https://rbtechngames\.com/[^"]+)"', block)
            product_url = url_m.group(1) if url_m else ""

            # Price: <bdi><span>&#8360;</span>38,999</bdi>
            price_m = re.search(
                r'woocommerce-Price-amount[^>]*><bdi>.*?</span>([\d,]+)</bdi>', block, re.DOTALL
            )
            price_pkr = int(price_m.group(1).replace(",", "")) if price_m else None

            # Thumbnail — prefer wp-post-image, fall back to any img in the box
            thumb_m = re.search(r'<img[^>]+class="[^"]*wp-post-image[^"]*"[^>]+src="([^"]+)"', block)
            if not thumb_m:
                thumb_m = re.search(r'<img[^>]+src="(https://rbtechngames\.com/wp-content/uploads/[^"]+)"', block)
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


def main():
    scraper = RbTechNGamesScraper()
    all_results: list[dict] = []

    for path, category in CATEGORIES:
        url = f"{BASE}/product-category/{path}/"
        print(f"\n[{category.upper()}] {url}")
        try:
            products = scraper.scrape(url)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue
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
