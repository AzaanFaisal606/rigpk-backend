"""
amdhouse.pk scraper — WooCommerce / Flatsome theme

How it works:
  - SSR HTML: product titles in .woocommerce-loop-product__title > a
  - Prices in <bdi><span class="woocommerce-Price-currencySymbol">&#8360;</span>79,000</bdi>
  - Thumbnails in <img class="attachment-woocommerce_thumbnail ... src="...">
  - Pagination: ?paged=N  (20 products per page)

Usage:
    python -m scrapers.amdhouse.scraper
"""

import html as _html
import re
import time

from scrapers.base_scraper import is_http_404
from scrapers.exceptions import ScrapeIncomplete
from scrapers.listing_scraper import ListingScraper, run_and_persist

SOURCE = "amdhouse.pk"
BASE = "https://amdhouse.pk"

# amdhouse splits its catalogue into flat sibling categories rather than a
# parent/child tree — "intel-motherboards" is not under "motherboards", and the
# two share no products. Several of ours must therefore map many slugs to one
# category. Slugs verified live 2026-07-21; `_find_valid_categories()` skips any
# that 404, so a retired slug costs one request, not a broken scrape.
CATEGORIES: list[tuple[str, str]] = [
    ("graphics-cards",          "gpu"),
    ("itx-graphics-cards",      "gpu"),
    ("processors",              "cpu"),
    ("intel-processors",        "cpu"),
    ("motherboards",            "motherboard"),
    ("intel-motherboards",      "motherboard"),
    ("itx-motherboards",        "motherboard"),
    ("ram",                     "ram"),
    ("storage-devices",         "ssd"),      # covers SSD + HDD
    ("pc-power-supplies",       "psu"),
    ("itx-psu",                 "psu"),
    ("pc-cases",                "case"),
    ("itx-pc-cases",            "case"),
    ("cpu-cooler",              "cooling"),
    ("low-profile-cpu-coolers", "cooling"),
    ("monitors",                "monitor"),
]


class AmdHouseScraper(ListingScraper):

    SOURCE = SOURCE

    # On a discounted card WooCommerce renders the original inside
    # <del>...<bdi>...</bdi></del> and the price you actually pay inside a
    # separate <ins>...<bdi>...</bdi></ins>. Sale price wins; regular price
    # is the fallback for undiscounted cards, which have no <ins>/<del> at all.
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

    def next_page_url(self, url: str, page: int) -> str:
        return f"{url}?paged={page}" if page > 1 else url

    def _extract_price(self, block: str) -> int | None:
        """Sale price wins over the crossed-out regular price."""
        for pattern in (self._SALE_PRICE_RE, self._PRICE_RE):
            m = pattern.search(block)
            if m:
                return self._to_int(m.group(1))
        return None

    def card_blocks(self, html: str) -> list[str]:
        # Product blocks — split on product div class. Bound the final block
        # at the theme's own "<footer id=\"footer\"" marker instead of
        # end-of-document: unbounded, the last card's block swallows the
        # entire page tail (widgets, footer copy), and any "out-of-stock"
        # text anywhere in it would falsely mark that last card sold out.
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
        # First chunk(s) without a title are the outer wrapper div, not a card
        return [b for b in blocks if 'woocommerce-loop-product__title' in b]

    def parse_card(self, block: str) -> dict | None:
        # Skip out-of-stock — Flatsome marks the card class "out-of-stock"
        # (hyphenated) and emits a <div class="out-of-stock-label">.
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
        url_m = re.search(
            r'woocommerce-LoopProduct-link[^"]*"\s+href="([^"]+)"', block
        )
        # also try title anchor href
        if not url_m:
            url_m = re.search(r'href="(https://amdhouse\.pk/product/[^"]+)"', block)
        product_url = url_m.group(1) if url_m else ""

        # Price: sale price wins over the crossed-out regular price
        price_pkr = self._extract_price(block)

        # Thumbnail
        thumb_m = re.search(
            r'<img[^>]+src="(https://amdhouse\.pk/wp-content/uploads/[^"]+)"', block
        )
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


def _find_valid_categories() -> list[tuple[str, str]]:
    """
    Filter CATEGORIES to only slugs that return products.

    A slug 404ing means the category was retired — safe to skip (H9's original,
    correct case). Any other probe failure (timeout, 5xx, HostBlocked, DNS) is
    NOT the same thing and must not be read as "category doesn't exist": that
    misreading is what silently shrank the scraped category list on a transient
    blip. Non-404 failures are collected and raised as ScrapeIncomplete once
    every slug has been probed, carrying whatever categories DID resolve on
    `.valid_categories` so the caller can still scrape those.
    """
    scraper = AmdHouseScraper()
    valid: list[tuple[str, str]] = []
    probe_errors: list[str] = []
    for slug, cat in CATEGORIES:
        url = f"{BASE}/product-category/{slug}/"
        try:
            html = scraper.fetch(url)
        except Exception as e:
            if is_http_404(e):
                print(f"  [skip] {slug}: 404 — category retired")
            else:
                print(f"  [probe failed] {slug}: {e}")
                probe_errors.append(f"{slug}: {e}")
            time.sleep(0.5)
            continue
        if 'woocommerce-loop-product__title' in html:
            valid.append((url, cat))
            print(f"  [ok] {slug} -> {cat}")
        else:
            print(f"  [empty] {slug}")
        time.sleep(0.5)

    if probe_errors:
        exc = ScrapeIncomplete(
            "amdhouse: category probe failed for " + "; ".join(probe_errors)
        )
        exc.valid_categories = valid
        raise exc

    return valid


def main():
    print("Checking available categories...")
    valid = _find_valid_categories()
    run_and_persist(AmdHouseScraper(), valid, write_db=True)


if __name__ == "__main__":
    main()
