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

import re

from scrapers.listing_scraper import ListingScraper, run_listing_cli, total_from_results_text

SOURCE = "zahcomputers.pk"
BASE = "https://zahcomputers.pk"
PAGE_SIZE = 32

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


class ZahComputersScraper(ListingScraper):
    """
    url should be the base category URL, e.g.:
    https://zahcomputers.pk/shop/?product_cat=graphics-cards
    Pagination is handled by prepending /page/N/ before the query string.
    """

    SOURCE = SOURCE

    def next_page_url(self, url: str, page: int) -> str:
        if page == 1:
            return url
        # Insert /page/N/ between /shop/ and the query string
        return re.sub(r"(/shop/)", rf"\g<1>page/{page}/", url)

    def extract_total(self, html: str) -> int | None:
        return total_from_results_text(html)

    def card_blocks(self, html: str) -> list[str]:
        return re.split(r'class="wd-product-wrapper', html)[1:]

    def parse_card(self, block: str) -> dict | None:
        _PLACEHOLDER = "woocommerce-placeholder"

        # Skip out-of-stock items
        if "outofstock" in block:
            return None

        # Name from aria-label on image anchor
        name_m = re.search(r'class="wd-product-img-link[^"]*"[^>]*aria-label="([^"]+)"', block)
        if not name_m:
            return None
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

        return {
            "name": name,
            "price_pkr": price_pkr,
            "url": url,
            "category": "",
            "source": SOURCE,
            "scraped_at": self.now(),
            "thumbnail_url": thumbnail,
        }


def main():
    run_listing_cli(
        ZahComputersScraper, CATEGORIES,
        lambda slug: f"{BASE}/shop/?product_cat={slug}",
        write_db=True,
    )


if __name__ == "__main__":
    main()
