"""
pakbyte.pk scraper — Shopify storefront

How it works:
  - SSR HTML; Shopify collection pages at /collections/<slug>?page=N (page 1 omits).
  - Product blocks wrapped in class="product-item product-item--vertical".
  - Title: <a class="product-item__title text--strong link">...</a>
  - URL:   /products/<slug>  (relative; needs BASE prefix)
  - Price: <span class="price">...Rs.234,990.00</span>  (Shopify renders Rs.X,XXX.XX)
  - Thumbnail: <img class="product-item__primary-image" src="//cdn.shopify.com/.../?width=100">
      Protocol-relative; bump width via param strip.
  - Total count: "X products" inside .collection__products-count-total.

Usage:
    python -m scrapers.pakbyte.scraper
"""

import html as _html
import re

from scrapers.listing_scraper import ListingScraper, run_listing_cli

SOURCE = "pakbyte.pk"
BASE = "https://www.pakbyte.pk"

# (category_slug_on_site, our_category_key)
CATEGORIES: list[tuple[str, str]] = [
    ("processors",     "cpu"),
    ("graphic-cards",  "gpu"),
    ("motherboards",   "motherboard"),
    ("memory-sticks",  "ram"),
    ("internal-ssd",   "ssd"),
    ("internal-hdd",   "hdd"),
    ("power-supplies", "psu"),
    ("casings",        "case"),
    ("cpu-coolers",    "cooling"),
    ("monitors",       "monitor"),
]


class PakByteScraper(ListingScraper):
    """
    url should be the base collection URL, e.g.:
    https://www.pakbyte.pk/collections/graphic-cards
    Pagination appends ?page=N.
    """

    SOURCE = SOURCE

    def extract_total(self, html: str) -> int | None:
        # PakByte renders "277 products" inside .collection__products-count-total
        m = re.search(
            r'collection__products-count-total[^>]*>\s*([\d,]+)\s+products',
            html,
        )
        if m:
            return int(m.group(1).replace(",", ""))
        # Fallback: any "N products" string
        m2 = re.search(r'of\s+([\d,]+)\s+products', html)
        if m2:
            return int(m2.group(1).replace(",", ""))
        return None

    def card_blocks(self, html: str) -> list[str]:
        return re.split(r'class="product-item product-item--vertical', html)[1:]

    def parse_card(self, block: str) -> dict | None:
        # Skip out-of-stock items (Shopify themes vary; check common markers)
        if "inventory--out" in block or "sold-out" in block:
            return None

        # URL — first /products/<slug> href in block
        url_m = re.search(r'href="(/products/[^"?#]+)"', block)
        if not url_m:
            return None
        slug = url_m.group(1)
        url = BASE + slug

        # Name from .product-item__title anchor text
        name_m = re.search(
            r'class="product-item__title[^"]*"[^>]*>([^<]+)</a>',
            block,
        )
        if not name_m:
            return None
        name = _html.unescape(name_m.group(1)).strip()

        # Price — Shopify renders e.g. "Rs.234,990.00" inside .price span
        price_pkr = self._parse_price_block(block)

        # Thumbnail — primary image src (protocol-relative, possibly ?width=100)
        thumbnail = self._parse_thumbnail(block)

        return {
            "name": name,
            "price_pkr": price_pkr,
            "url": url,
            "category": "",
            "source": SOURCE,
            "scraped_at": self.now(),
            "thumbnail_url": thumbnail,
        }

    @staticmethod
    def _parse_price_block(block: str) -> int | None:
        """
        Shopify price markup (PakByte theme):
          <span class="price">
            <span class="visually-hidden">Sale price</span>Rs.234,990.00
          </span>
        Sale layout also exposes <s class="price--original"> for the strikethrough.
        Strategy: locate the .price span, strip any nested span tags, then grab
        the Rs.XXX,XXX number (dropping trailing .NN cents).
        """
        # Match the .price block as a whole including any inner spans.
        # Greedy on the body, anchor on </span> at the END of the price element.
        m = re.search(
            r'<span[^>]*class="price[^"]*"[^>]*>(.*?)</span>(?:\s*</div>|\s*<)',
            block, re.DOTALL,
        )
        if not m:
            return None
        inner = m.group(1)
        # Drop any nested spans (e.g. visually-hidden "Sale price" label)
        inner = re.sub(r'<span[^>]*>.*?</span>', '', inner, flags=re.DOTALL)
        # Strip tags + entities, keep visible text
        inner = re.sub(r'<[^>]+>', '', inner)
        inner = _html.unescape(inner)
        # Match "Rs.234,990.00", "Rs 234,990", "PKR 1,20,000" — digits with commas
        price_m = re.search(r'([\d][\d,]*?)(?:\.\d+)?(?:\s|$)', inner)
        if not price_m:
            # Fallback: any digit run
            price_m = re.search(r'([\d,]+)', inner)
            if not price_m:
                return None
        digits = price_m.group(1).replace(",", "")
        if not digits:
            return None
        return int(digits)

    @staticmethod
    def _parse_thumbnail(block: str) -> str | None:
        """
        Primary image: <img ... class="product-item__primary-image" src="//cdn.shopify.com/...?width=100">
        Returns protocol-prefixed URL with width param stripped (request larger image).
        """
        # Prefer the explicit primary-image; fall back to first <img> with shopify CDN.
        m = re.search(
            r'<img[^>]+src="([^"]+)"[^>]+class="product-item__primary-image"',
            block,
        )
        if not m:
            # Alternative attribute order: class first, then src
            m = re.search(
                r'<img[^>]*class="product-item__primary-image"[^>]+src="([^"]+)"',
                block,
            )
        if not m:
            return None
        src = m.group(1)
        # Skip placeholders
        if "placeholder" in src.lower() or "no-image" in src.lower():
            return None
        # Protocol-relative to absolute
        if src.startswith("//"):
            src = "https:" + src
        # Drop shopify width=100 param — too small for site UI
        src = re.sub(r'([?&])width=\d+(&|$)', r'\1', src)
        # Clean any trailing ? or stray &
        src = src.rstrip("?&")
        return src


def main():
    """Standalone smoke test — does NOT write to DB."""
    run_listing_cli(
        PakByteScraper, CATEGORIES,
        lambda slug: f"{BASE}/collections/{slug}",
        write_db=False, default_filter="gpu",
    )


if __name__ == "__main__":
    main()
