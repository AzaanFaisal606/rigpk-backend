"""
redtech.pk scraper — WooCommerce / Woodmart theme

How it works:
  - SSR HTML; category URLs use /product-category/ prefix:
    https://redtech.pk/product-category/{slug}/
  - Product names in aria-label on the product image link.
  - Prices: <span class="woocommerce-Price-amount amount"><bdi><span>&#8360;</span>XX,XXX</bdi>
  - Thumbnails: lazy-loaded; real URL in data-src on <img> inside .wd-product-img-link.
    Fallback: data-image-url on .wd-product-grid-slide if present.
  - Out-of-stock: block class "outofstock" token OR stock <p> class "out-of-stock".
  - Pagination: /product-category/{slug}/page/N/  (page 1 omits /page/N/). Page size ~12-24.
  - Total count: "of X results" in HTML.

Usage:
    python -m scrapers.redtech.scraper          # GPU only (smoke test)
    python -m scrapers.redtech.scraper --all    # all categories
"""

import html as _html
import re
import sys
import time

from scrapers.base_scraper import BaseScraper

SOURCE = "redtech.pk"
BASE = "https://redtech.pk"
PAGE_DELAY = 1.2

CATEGORIES: list[tuple[str, str]] = [
    ("processor",       "cpu"),
    ("graphic-card",    "gpu"),
    ("motherboard",     "motherboard"),
    ("ram",             "ram"),
    ("ssd-nvme",        "ssd"),
    ("hard-drive",      "hdd"),
    ("power-supply",    "psu"),
    ("gaming-case",     "case"),
    ("cpu-cooler",      "cooling"),
    ("gaming-monitors", "monitor"),
]

def _is_sold_out(block: str) -> bool:
    """Two-layer skip:
       1. The product card's outer 'wd-product ... outofstock' class token (Woodmart).
       2. The stock paragraph emits 'out-of-stock' (hyphenated) as a class.
    """
    cls_m = re.match(r'<div[^>]+class="([^"]*)"', block)
    leading_classes = cls_m.group(1) if cls_m else ""
    if "outofstock" in leading_classes:
        return True
    if re.search(r'<p[^>]+class="[^"]*wd-product-stock[^"]*out-of-stock', block):
        return True
    return False


class RedTechScraper(BaseScraper):

    def scrape(self, url: str) -> list[dict]:
        """
        url should be the base category URL, e.g.:
        https://redtech.pk/product-category/graphic-card/
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

        blocks = re.split(r'(?=<div[^>]+class="wd-product-wrapper)', html)[1:]
        if not blocks:
            return []

        results = []
        for block in blocks:
            if _is_sold_out(block):
                continue

            # Name from aria-label on image anchor
            name_m = re.search(
                r'class="wd-product-img-link[^"]*"[^>]*aria-label="([^"]+)"',
                block,
            )
            if not name_m:
                # Fallback: title anchor in wd-entities-title
                name_m = re.search(
                    r'class="wd-entities-title"[^>]*>\s*<[^>]+>([^<]+)<',
                    block,
                )
            if not name_m:
                continue
            name = _html.unescape(name_m.group(1)).strip()

            # URL — prefer href on image anchor, fallback to any product link
            url_m = re.search(
                r'class="wd-product-img-link[^"]*"\s+href="(https://redtech\.pk/[^"?#]+)"',
                block,
            )
            if not url_m:
                url_m = re.search(
                    r'href="(https://redtech\.pk/product/[^"?#]+)"',
                    block,
                )
            product_url = url_m.group(1) if url_m else ""

            # Price: prefer <ins> sale price, fall back to regular price
            ins_m = re.search(
                r'<ins[^>]*>.*?<bdi>\s*<span[^>]*woocommerce-Price-currencySymbol[^>]*>[^<]*</span>(?:&nbsp;|\s)*([\d,]+)',
                block, re.S,
            )
            if ins_m:
                price_pkr: int | None = int(ins_m.group(1).replace(",", ""))
            else:
                reg_m = re.search(
                    r'<bdi>\s*<span[^>]*woocommerce-Price-currencySymbol[^>]*>[^<]*</span>(?:&nbsp;|\s)*([\d,]+)',
                    block, re.S,
                )
                price_pkr = int(reg_m.group(1).replace(",", "")) if reg_m else None

            # Thumbnail: lazy-loaded — real URL in data-src; fallback data-image-url
            thumbnail: str | None = None
            data_img_m = re.search(
                r'class="wd-product-grid-slide[^"]*"[^>]*data-image-url="([^"]+)"',
                block,
            )
            if data_img_m and _PLACEHOLDER not in data_img_m.group(1):
                thumbnail = data_img_m.group(1)
            else:
                # data-src on <img> inside image anchor (lazy-load)
                img_m = re.search(
                    r'class="wd-product-img-link[^"]*"[^>]*>.*?<img[^>]+data-src="([^"]+)"',
                    block, re.S,
                )
                if img_m and _PLACEHOLDER not in img_m.group(1):
                    thumbnail = img_m.group(1)
                else:
                    # Last resort: real src (non-placeholder, non-lazy.svg)
                    src_m = re.search(
                        r'class="wd-product-img-link[^"]*"[^>]*>.*?<img[^>]+src="([^"]+)"',
                        block, re.S,
                    )
                    if src_m and _PLACEHOLDER not in src_m.group(1) and "lazy" not in src_m.group(1):
                        thumbnail = src_m.group(1)

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
    def _extract_total(html: str) -> int | None:
        m = re.search(r"of\s+([\d,]+)\s+results", html, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(",", ""))
        return None


def main():
    """Standalone smoke test — does NOT write to DB."""
    scraper = RedTechScraper()
    all_results: list[dict] = []

    only_gpu = "--all" not in sys.argv
    cats = [c for c in CATEGORIES if c[1] == "gpu"] if only_gpu else CATEGORIES

    for slug, category in cats:
        url = f"{BASE}/product-category/{slug}/"
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
