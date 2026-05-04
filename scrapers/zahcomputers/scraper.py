"""
zahcomputers.pk scraper — WooCommerce / Woodmart theme

How it works:
  - SSR HTML contains product names in aria-label on the product image link.
  - Prices in <span class="woocommerce-Price-amount amount"><bdi><span>&#8360;</span>XX,XXX</bdi>
  - Thumbnails in data-image-url="..." on the first .wd-product-grid-slide per product.
  - Pagination: /shop/page/N/?product_cat=SLUG  (32 products per page)
  - Total count: "X of N results" in HTML.

Usage:
    python -m scrapers.zahcomputers.scraper
"""

import os
import re
import sys
import time

from scrapers.base_scraper import BaseScraper

SOURCE = "zahcomputers.pk"
BASE = "https://zahcomputers.pk"
PAGE_SIZE = 32
PAGE_DELAY = 1.2

# (category_slug_on_site, our_category_key)
CATEGORIES: list[tuple[str, str]] = [
    ("graphics-cards",      "gpu"),
    ("processors",          "cpu"),
    ("motherboard-chipset", "motherboard"),
    ("memory-module-ram",   "ram"),
    ("sata-ssd",            "ssd"),
    ("power-supplies",      "psu"),
    ("casing",              "case"),
    ("cooling-solutions",   "cooling"),
    ("monitors",            "monitor"),
]


class ZahComputersScraper(BaseScraper):

    def scrape(self, url: str) -> list[dict]:
        """
        url should be the base category URL, e.g.:
        https://zahcomputers.pk/shop/?product_cat=graphics-cards
        Pagination is handled by prepending /page/N/ before the query string.
        """
        all_products: list[dict] = []
        seen_urls: set[str] = set()
        total: int | None = None
        page = 1

        while True:
            # Build paginated URL: /shop/page/2/?product_cat=...
            if page == 1:
                page_url = url
            else:
                # Insert /page/N/ between /shop/ and the query string
                page_url = re.sub(r"(/shop/)", rf"\g<1>page/{page}/", url)

            print(f"    page {page}: {page_url}")
            html = self.fetch(page_url)

            if page == 1:
                total = self._extract_total(html)
                if total is not None:
                    print(f"    total reported: {total}")

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
            if total is not None and len(all_products) >= total:
                print(f"    collected all {total} — done.")
                break

            page += 1
            time.sleep(PAGE_DELAY)

        return all_products

    def _parse_page(self, html: str) -> list[dict]:
        scraped_at = self.now()
        _PLACEHOLDER = "woocommerce-placeholder"

        blocks = re.split(r'class="wd-product-wrapper', html)[1:]
        if not blocks:
            return []

        results = []
        for block in blocks:
            # Skip out-of-stock items
            if "outofstock" in block:
                continue

            # Name from aria-label on image anchor
            name_m = re.search(r'class="wd-product-img-link[^"]*"[^>]*aria-label="([^"]+)"', block)
            if not name_m:
                continue
            name = name_m.group(1).strip()

            # URL
            url_m = re.search(r'href="(https://zahcomputers\.pk/product/[^"]+)"', block)
            url = url_m.group(1) if url_m else ""

            # Price: prefer <ins> (sale price) over regular price
            ins_m = re.search(
                r'<ins[^>]*>.*?woocommerce-Price-amount[^>]*><bdi>[^<]*<span[^>]+>[^<]*</span>([\d,]+)',
                block, re.DOTALL
            )
            if ins_m:
                price_pkr = int(ins_m.group(1).replace(",", ""))
            else:
                reg_m = re.search(
                    r'woocommerce-Price-amount amount"><bdi><span[^>]+>[^<]+</span>([\d,]+)',
                    block
                )
                price_pkr = int(reg_m.group(1).replace(",", "")) if reg_m else None

            # Thumbnail from data-image-url on first .wd-product-grid-slide
            thumbnail = None
            data_img_m = re.search(r'class="wd-product-grid-slide[^"]*"[^>]*data-image-url="([^"]+)"', block)
            if data_img_m and _PLACEHOLDER not in data_img_m.group(1):
                thumbnail = data_img_m.group(1)

            results.append({
                "name": name,
                "price_pkr": price_pkr,
                "url": url,
                "category": "",
                "source": SOURCE,
                "scraped_at": scraped_at,
                "thumbnail_url": thumbnail,
            })

        return results

    @staticmethod
    def _extract_total(html: str) -> int | None:
        m = re.search(r"of\s+([\d,]+)\s+results", html, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(",", ""))
        return None

def main():
    scraper = ZahComputersScraper()
    all_results: list[dict] = []

    for slug, category in CATEGORIES:
        url = f"{BASE}/shop/?product_cat={slug}"
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

    # Write to DB
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
