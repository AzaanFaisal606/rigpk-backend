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
import re

from scrapers.listing_scraper import ListingScraper, run_listing_cli, total_from_results_text

SOURCE = "techarc.pk"
BASE = "https://techarc.pk"

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


class TechArcScraper(ListingScraper):
    """
    url should be the base category URL, e.g.:
    https://techarc.pk/graphics-cards/
    Pagination appends /page/N/ to the URL.
    """

    SOURCE = SOURCE

    def next_page_url(self, url: str, page: int) -> str:
        base_url = url.rstrip("/") + "/"
        return base_url if page == 1 else f"{base_url}page/{page}/"

    def extract_total(self, html: str) -> int | None:
        return total_from_results_text(html)

    def card_blocks(self, html: str) -> list[str]:
        # Same delimiter as zah; works on both wd-product-wrapper and wd-product wd-col
        return re.split(r'class="wd-product-wrapper', html)[1:]

    def parse_card(self, block: str) -> dict | None:
        _PLACEHOLDER = "woocommerce-placeholder"

        # Skip out-of-stock items
        if "outofstock" in block:
            return None

        # Name from aria-label on image anchor
        name_m = re.search(
            r'class="wd-product-img-link[^"]*"[^>]*aria-label="([^"]+)"',
            block,
        )
        if not name_m:
            return None
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
    """Standalone smoke test — does NOT write to DB."""
    run_listing_cli(
        TechArcScraper, CATEGORIES,
        lambda slug: f"{BASE}/{slug}/",
        write_db=False, default_filter="gpu",
    )


if __name__ == "__main__":
    main()
