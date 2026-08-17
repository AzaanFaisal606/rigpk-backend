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

from scrapers.listing_scraper import ListingScraper, run_listing_cli, total_from_results_text

SOURCE = "redtech.pk"
BASE = "https://redtech.pk"

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


class RedTechScraper(ListingScraper):
    """
    url should be the base category URL, e.g.:
    https://redtech.pk/product-category/graphic-card/
    Pagination appends /page/N/ to the URL.
    """

    SOURCE = SOURCE

    def next_page_url(self, url: str, page: int) -> str:
        base_url = url.rstrip("/") + "/"
        return base_url if page == 1 else f"{base_url}page/{page}/"

    def extract_total(self, html: str) -> int | None:
        return total_from_results_text(html)

    def card_blocks(self, html: str) -> list[str]:
        return re.split(r'(?=<div[^>]+class="wd-product-wrapper)', html)[1:]

    def parse_card(self, block: str) -> dict | None:
        _PLACEHOLDER = "woocommerce-placeholder"

        if _is_sold_out(block):
            return None

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
            return None
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
    """Standalone smoke test — does NOT write to DB."""
    run_listing_cli(
        RedTechScraper, CATEGORIES,
        lambda slug: f"{BASE}/product-category/{slug}/",
        write_db=False, default_filter="gpu",
    )


if __name__ == "__main__":
    main()
