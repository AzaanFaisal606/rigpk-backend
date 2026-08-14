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

import html as _html
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

    # Same Flatsome/WooCommerce discount markup as amdhouse — sale price is
    # in <ins>, the crossed-out original in <del>. Sale wins; regular price
    # is the fallback for undiscounted cards (no <ins>/<del> at all).
    _SALE_PRICE_RE = re.compile(
        r'<ins[^>]*>.*?woocommerce-Price-amount[^>]*><bdi>.*?</span>([\d,]+)</bdi>',
        re.DOTALL,
    )
    _PRICE_RE = re.compile(
        r'woocommerce-Price-amount[^>]*><bdi>.*?</span>([\d,]+)</bdi>', re.DOTALL
    )

    @staticmethod
    def _to_int(raw: str) -> int:
        return int(raw.replace(",", ""))

    def _extract_price(self, block: str) -> int | None:
        """Sale price wins over the crossed-out regular price."""
        for pattern in (self._SALE_PRICE_RE, self._PRICE_RE):
            m = pattern.search(block)
            if m:
                return self._to_int(m.group(1))
        return None

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
        scraped_at = self.now()

        # Split into product blocks. Bound the final block at the theme's
        # own "<footer id=\"footer\"" marker instead of end-of-document: an
        # unbounded last block swallows the whole page tail (pagination nav,
        # footer copy), and any "out-of-stock" text in it would falsely mark
        # that last card sold out.
        matches = list(re.finditer(r'<div[^>]+class="[^"]*product-small\s', html))
        blocks = []
        for i, m in enumerate(matches):
            start = m.start()
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                footer_idx = html.find('<footer id="footer"', start)
                end = footer_idx if footer_idx != -1 else len(html)
            blocks.append(html[start:end])
        blocks = [b for b in blocks if 'woocommerce-loop-product__title' in b]

        if not blocks:
            return []

        results = []
        for block in blocks:
            # Skip out-of-stock — Flatsome marks the card class "out-of-stock"
            # (same token as amdhouse).
            if "out-of-stock" in block:
                continue

            # Name
            name_m = re.search(
                r'woocommerce-loop-product__title[^>]*><a[^>]+>([^<]+)', block
            )
            if not name_m:
                continue
            name = _html.unescape(name_m.group(1)).strip()

            # URL
            url_m = re.search(r'href="(https://rbtechngames\.com/[^"]+)"[^>]*class="woocommerce-LoopProduct', block)
            if not url_m:
                url_m = re.search(r'href="(https://rbtechngames\.com/shop[^"]+)"', block)
            if not url_m:
                url_m = re.search(r'href="(https://rbtechngames\.com/[^"]+)"', block)
            product_url = url_m.group(1) if url_m else ""

            # Price: sale price wins over the crossed-out regular price
            price_pkr = self._extract_price(block)

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
