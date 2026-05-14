"""
techarc.pk scraper — WooCommerce / Woodmart theme

How it works:
  - SSR HTML; permalinks strip /product-category/, so category URLs are flat:
    https://techarc.pk/graphics-cards/
  - Product names in aria-label on the product image link.
  - Prices: <span class="woocommerce-Price-amount amount"><bdi><span>&#8360;</span>XX,XXX</bdi>
  - Thumbnails: <img src="..."> inside .wd-product-img-link (eager-loaded, no lazy attr).
    Fallback: data-image-url on .wd-product-grid-slide if Woodmart lazy-loading kicks in.
  - Pagination: /<slug>/page/N/  (page 1 omits /page/N/). Page size ~10-20.
  - Total count: "of X results" in HTML.

Usage:
    python -m scrapers.techarc.scraper
"""

import html as _html
import os
import re
import sys
import time

from scrapers.base_scraper import BaseScraper

SOURCE = "techarc.pk"
BASE = "https://techarc.pk"
PAGE_DELAY = 1.2

# (category_slug_on_site, our_category_key)
# Tech Arc uses flat slugs (no /product-category/ prefix).
# HDD intentionally skipped: no dedicated leaf, /storage/ duplicates SSD.
# /cpu-coolers/ parent kept: covers both air + AIO, matches PPC "cooling" category.
CATEGORIES: list[tuple[str, str]] = [
    ("cpu-processors",             "cpu"),
    ("graphics-cards",             "gpu"),
    ("motherboards",               "motherboard"),
    ("ram-memory-modules",         "ram"),
    ("storage/solid-state-drives", "ssd"),
    ("power-supplies",             "psu"),
    ("cases",                      "case"),
    ("cpu-coolers",                "cooling"),
    ("monitors",                   "monitor"),
]


class TechArcScraper(BaseScraper):

    def scrape(self, url: str) -> list[dict]:
        """
        url should be the base category URL, e.g.:
        https://techarc.pk/graphics-cards/
        Pagination appends /page/N/ to the URL.
        """
        all_products: list[dict] = []
        seen_urls: set[str] = set()
        total: int | None = None
        page = 1
        consecutive_errors = 0

        base_url = url.rstrip("/") + "/"

        while True:
            page_url = base_url if page == 1 else f"{base_url}page/{page}/"

            print(f"    page {page}: {page_url}")
            try:
                html = self.fetch(page_url)
                consecutive_errors = 0
            except RuntimeError as e:
                consecutive_errors += 1
                print(f"    page {page} failed: {e}")
                if consecutive_errors >= 3:
                    print(f"    stopping after {consecutive_errors} consecutive errors")
                    break
                page += 1
                time.sleep(PAGE_DELAY)
                continue

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

        # Same delimiter as zah; works on both wd-product-wrapper and wd-product wd-col
        blocks = re.split(r'class="wd-product-wrapper', html)[1:]
        if not blocks:
            return []

        results = []
        for block in blocks:
            # Skip out-of-stock items
            if "outofstock" in block:
                continue

            # Name from aria-label on image anchor
            name_m = re.search(
                r'class="wd-product-img-link[^"]*"[^>]*aria-label="([^"]+)"',
                block,
            )
            if not name_m:
                continue
            name = _html.unescape(name_m.group(1)).strip()

            # URL — same anchor's href
            url_m = re.search(
                r'class="wd-product-img-link[^"]*"\s+href="(https://techarc\.pk/[^"?#]+)"',
                block,
            )
            if not url_m:
                # Fallback: any product anchor href on this domain
                url_m = re.search(
                    r'href="(https://techarc\.pk/[^"?#]+/)"',
                    block,
                )
            url = url_m.group(1) if url_m else ""

            # Price: prefer <ins> (sale price) over regular price
            ins_m = re.search(
                r'<ins[^>]*>.*?woocommerce-Price-amount[^>]*><bdi>[^<]*<span[^>]+>[^<]*</span>([\d,]+)',
                block, re.DOTALL,
            )
            if ins_m:
                price_pkr = int(ins_m.group(1).replace(",", ""))
            else:
                reg_m = re.search(
                    r'woocommerce-Price-amount amount"><bdi><span[^>]+>[^<]+</span>([\d,]+)',
                    block,
                )
                price_pkr = int(reg_m.group(1).replace(",", "")) if reg_m else None

            # Thumbnail: try data-image-url first (Woodmart lazy-load),
            # then real <img src> inside the image link.
            thumbnail: str | None = None
            data_img_m = re.search(
                r'class="wd-product-grid-slide[^"]*"[^>]*data-image-url="([^"]+)"',
                block,
            )
            if data_img_m and _PLACEHOLDER not in data_img_m.group(1):
                thumbnail = data_img_m.group(1)
            else:
                img_m = re.search(
                    r'class="wd-product-img-link[^"]*"[^>]*>\s*<img[^>]+src="([^"]+)"',
                    block,
                )
                if img_m and _PLACEHOLDER not in img_m.group(1):
                    thumbnail = img_m.group(1)

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
    """Standalone smoke test — does NOT write to DB."""
    scraper = TechArcScraper()
    all_results: list[dict] = []

    # Smoke test: scrape only GPU category if invoked with no arg,
    # else all categories.
    only_gpu = "--all" not in sys.argv

    cats = [c for c in CATEGORIES if c[1] == "gpu"] if only_gpu else CATEGORIES

    for slug, category in cats:
        url = f"{BASE}/{slug}/"
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

    # First 3 samples for visual verification
    print("\n--- sample products ---")
    for p in all_results[:3]:
        print(f"  name:  {p['name'][:80]}")
        print(f"  price: {p['price_pkr']}")
        print(f"  url:   {p['url']}")
        print(f"  thumb: {(p['thumbnail_url'] or '')[:80]}")
        print()

    print("(no DB write — standalone smoke test)")


if __name__ == "__main__":
    main()
