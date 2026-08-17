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

from scrapers.listing_scraper import ListingScraper, run_listing_cli, total_from_results_text

SOURCE = "techmatched.pk"
BASE = "https://techmatched.pk"

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


class TechMatchedScraper(ListingScraper):
    """
    url should be the base category URL, e.g.:
    https://techmatched.pk/product-category/processors/
    Pagination appends /page/N/ to the URL.
    """

    SOURCE = SOURCE

    def next_page_url(self, url: str, page: int) -> str:
        base_url = url.rstrip("/") + "/"
        return base_url if page == 1 else f"{base_url}page/{page}/"

    def extract_total(self, html: str) -> int | None:
        return total_from_results_text(html)

    def card_blocks(self, html: str) -> list[str]:
        # Split on <li class="product type-product ..."> boundaries
        matches = list(re.finditer(r'<li class="product type-product[^"]*"[^>]*>', html))
        if not matches:
            return []

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
        return blocks

    def parse_card(self, block: str) -> dict | None:
        _PLACEHOLDER = "woocommerce-placeholder"

        if _is_sold_out(block):
            return None

        # Name from <h2 class="woocommerce-loop-product__title"> inner anchor
        name_m = re.search(
            r'class="woocommerce-loop-product__title"[^>]*>\s*<a[^>]*>([^<]+)</a>',
            block, re.S,
        )
        if not name_m:
            return None
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

        # Price — try JSON dataLayer first (exact integer rupees). No bare
        # "price": N fallback here — a card can embed a related-product
        # widget ahead of its own markup, and an unscoped regex picks
        # that neighbour's price instead of the card's own (M5).
        price_pkr: int | None = None
        dl_m = re.search(
            r'wpmDataLayer\)\.products\[\d+\]\s*=\s*\{[^}]*"price"\s*:\s*(\d+)',
            block,
        )
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
        TechMatchedScraper, CATEGORIES,
        lambda slug: f"{BASE}/product-category/{slug}/",
        write_db=False, default_filter="cpu",
    )


if __name__ == "__main__":
    main()
