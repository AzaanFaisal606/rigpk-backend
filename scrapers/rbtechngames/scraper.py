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
import re

from scrapers.listing_scraper import ListingScraper, run_listing_cli

SOURCE = "rbtechngames.com"
BASE = "https://rbtechngames.com"

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


class RbTechNGamesScraper(ListingScraper):

    SOURCE = SOURCE

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

    def next_page_url(self, url: str, page: int) -> str:
        return f"{url}/page/{page}/" if page > 1 else url

    def card_blocks(self, html: str) -> list[str]:
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
        return [b for b in blocks if 'woocommerce-loop-product__title' in b]

    def parse_card(self, block: str) -> dict | None:
        # Skip out-of-stock — Flatsome marks the card class "out-of-stock"
        # (same token as amdhouse).
        if "out-of-stock" in block:
            return None

        # Name
        name_m = re.search(
            r'woocommerce-loop-product__title[^>]*><a[^>]+>([^<]+)', block
        )
        if not name_m:
            return None
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

        return {
            "name": name,
            "price_pkr": price_pkr,
            "url": product_url,
            "category": "",
            "source": SOURCE,
            "scraped_at": self.now(),
            "thumbnail_url": thumbnail,
        }


def main():
    run_listing_cli(
        RbTechNGamesScraper, CATEGORIES,
        lambda path: f"{BASE}/product-category/{path}/",
        write_db=True,
    )


if __name__ == "__main__":
    main()
