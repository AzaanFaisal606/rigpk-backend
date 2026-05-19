"""
techmatched.pk scraper — WooCommerce / Woostify theme

How it works:
  - SSR HTML; category URLs use /product-category/ prefix:
    https://techmatched.pk/product-category/{slug}/
  - Product names in <h2 class="woocommerce-loop-product__title"> title anchor.
    Names include "Buy " prefix and " | TechMatched" suffix — stripped on extraction.
  - Prices: JSON dataLayer (most reliable) or woocommerce-Price-amount markup fallback.
  - Thumbnails: <img src="..."> inside the product image link wrapper.
    Strip WooCommerce size suffix (-NNNxNNN) for full-size image.
  - Out-of-stock: <li class="product type-product ... outofstock ..."> token on the
    card element, or stock <p class="... stock out-of-stock ..."> element.
  - Pagination: /product-category/{slug}/page/N/  (page 1 omits /page/N/). Page size ~24-36.
  - Total count: "of X results" in HTML.

Usage:
    python -m scrapers.techmatched.scraper          # CPU only (smoke test)
    python -m scrapers.techmatched.scraper --all    # all categories
"""

import html as _html
import re
import sys
import time

from scrapers.base_scraper import BaseScraper

SOURCE = "techmatched.pk"
BASE = "https://techmatched.pk"
PAGE_DELAY = 1.2

CATEGORIES: list[tuple[str, str]] = [
    ("processors",                                              "cpu"),
    ("graphics-card-in-pakistan",                               "gpu"),
    ("motherboards",                                            "motherboard"),
    ("rams",                                                    "ram"),
    ("storage-in-pakistan/find-ssd-prices-in-pakistan",         "ssd"),
    ("storage-in-pakistan/hard-drive",                          "hdd"),
    ("buy-power-supply-in-pakistan",                            "psu"),
    ("computer-case",                                           "case"),
    ("cpu-coolers",                                             "cooling"),
    ("gaming-monitors",                                         "monitor"),
]


def _is_sold_out(block: str) -> bool:
    """Two-layer skip:
       1. The product card's outer <li> 'outofstock' class token (Woostify).
       2. Defensive: stock badge <p> with 'stock' and 'out-of-stock' classes.
    """
    cls_m = re.match(r'<li[^>]+class="([^"]*)"', block)
    leading = cls_m.group(1) if cls_m else ""
    if "outofstock" in leading:
        return True
    # Defensive: if Woostify ever moves to a stock badge element
    if re.search(r'<p[^>]+class="[^"]*\bstock\b[^"]*\bout-of-stock\b', block):
        return True
    return False


class TechMatchedScraper(BaseScraper):

    def scrape(self, url: str) -> list[dict]:
        """
        url should be the base category URL, e.g.:
        https://techmatched.pk/product-category/processors/
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

        # Split on <li class="product type-product ..."> boundaries
        matches = list(re.finditer(r'<li class="product type-product[^"]*"[^>]*>', html))
        if not matches:
            return []

        # Build block slices
        blocks = []
        for i, m in enumerate(matches):
            start = m.start()
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                # Last block: up to next </ul> or end of HTML
                ul_end = html.find("</ul>", m.start())
                end = ul_end if ul_end != -1 else len(html)
            blocks.append(html[start:end])

        results = []
        for block in blocks:
            if _is_sold_out(block):
                continue

            # Name from <h2 class="woocommerce-loop-product__title"> inner anchor
            name_m = re.search(
                r'class="woocommerce-loop-product__title"[^>]*>\s*<a[^>]*>([^<]+)</a>',
                block, re.S,
            )
            if not name_m:
                continue
            name = _html.unescape(name_m.group(1)).strip()

            # Strip marketing affixes added by TechMatched
            if name.lower().startswith("buy "):
                name = name[4:].strip()
            if name.lower().endswith("| techmatched"):
                name = name[: -len("| techmatched")].rstrip(" |").strip()

            # URL — first product link in block
            url_m = re.search(
                r'<a[^>]+class="[^"]*woocommerce-LoopProduct-link[^"]*"[^>]+href="(https://techmatched\.pk/product/[^"?#]+)"',
                block,
            )
            if not url_m:
                url_m = re.search(
                    r'href="(https://techmatched\.pk/product/[^"?#]+)"',
                    block,
                )
            product_url = url_m.group(1) if url_m else ""

            # Price — try JSON dataLayer first (exact integer rupees)
            price_pkr: int | None = None
            dl_m = re.search(
                r'wpmDataLayer\)\.products\[\d+\]\s*=\s*\{[^}]*"price"\s*:\s*(\d+)',
                block,
            )
            if not dl_m:
                # Broader: any "price": N in the block's script
                dl_m = re.search(r'"price"\s*:\s*(\d+)', block)
            if dl_m:
                price_pkr = int(dl_m.group(1))
            else:
                # Fallback: WooCommerce visible markup
                price_m = re.search(
                    r'<span class="woocommerce-Price-amount amount"><span class="woocommerce-Price-currencySymbol">[^<]*</span>([\d,]+)',
                    block,
                )
                if price_m:
                    price_pkr = int(price_m.group(1).replace(",", ""))

            # Thumbnail — <img src="..."> inside the product image link
            thumbnail: str | None = None
            img_m = re.search(
                r'<img[^>]+src="(https://techmatched\.pk/wp-content/uploads/[^"]+)"',
                block,
            )
            if img_m and _PLACEHOLDER not in img_m.group(1):
                raw_url = img_m.group(1)
                # Strip WooCommerce size suffix e.g. -300x300 before extension
                thumbnail = re.sub(r'-\d+x\d+(?=\.\w+$)', '', raw_url)

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
    scraper = TechMatchedScraper()
    all_results: list[dict] = []

    only_cpu = "--all" not in sys.argv
    cats = [c for c in CATEGORIES if c[1] == "cpu"] if only_cpu else CATEGORIES

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
