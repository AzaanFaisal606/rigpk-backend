"""
zahcomputers.pk — HTML listing scraper

The default zah source while the Store API is blocked: zah's
/wp-json/wc/store/v1/products now returns a Cloudflare 403 for our UA. The API
scraper (scrapers/zah/scraper.py) stays as is, to switch back to once zah
allowlists us. Categories and SOURCE are shared with it.

How it works:
  - zah's custom storefront SSR-renders a category listing at
    https://zahcomputers.pk/category/<slug>/ (trailing slash; without it 301s).
  - Pagination uses ?page=N, 24 cards a page. Past the last page the site
    returns a 200 with no cards, so the template loop stops on "no new
    products". robots.txt allows ?page=N; never send filter, sort, brand,
    price or stock_status params.
  - One <article class="ProductCard-module__…__card"> per product. Name from
    the title link's title attribute, price from the Price-module "now" span.
    CSS-module hashes change on every rebuild, so classes match by prefix.
  - Sold-out cards ("Sold out" badge, price "Contact") are interleaved with
    in-stock ones and skipped, as is any other unpriced card.
  - The hero "N in stock now" count includes sold-out cards, so it matches
    ListingScraper's cards_seen.

No Playwright needed — plain HTTP requests work.

Usage:
    python -m scrapers.zah.html.scraper          # GPU only (smoke test, no DB write)
    python -m scrapers.zah.html.scraper --all    # all categories
"""

import html
import re

from scrapers.listing_scraper import ListingScraper, run_listing_cli
from scrapers.zah.scraper import ZahScraper

SOURCE = ZahScraper.SOURCE
BASE = ZahScraper.BASE
CATEGORIES = ZahScraper.CATEGORIES


def category_url(slug: str) -> str:
    return f"{BASE}/category/{slug}/"


class ZahHtmlScraper(ListingScraper):

    SOURCE = SOURCE
    MAX_PAGES = 200                  # zah's largest category is ~20 pages; 200 is pure insurance

    # Card boundary. Every field is read from within one card's block, so a
    # field missing on one card can never shift onto the next.
    _CARD_START_RE = re.compile(r'<article class="ProductCard-module__[^"]*__card"')
    _TITLE_RE = re.compile(
        r'class="ProductCard-module__[^"]*__title"[^>]*>\s*<a href="([^"]+)" title="([^"]*)"'
    )
    _PRICE_RE = re.compile(r'class="Price-module__[^"]*__now[^"]*"[^>]*>([^<]*)<')
    _SOLD_OUT_RE = re.compile(r'class="ProductCard-module__[^"]*__out"')
    _THUMB_RE = re.compile(r'<img[^>]+src="([^"]+)"')
    _TOTAL_RE = re.compile(r'id="zahHeroTotalCount"[^>]*>\s*([\d,]+)\s*<')

    # The API scraper's clean_name(), so a name comes out byte-identical to
    # what the Store API path stored for the same product.
    _api_names = ZahScraper()

    def card_blocks(self, html_text: str) -> list[str]:
        """Split a listing page into one block per product card."""
        starts = [m.start() for m in self._CARD_START_RE.finditer(html_text)]
        if not starts:
            return []
        starts.append(len(html_text))
        return [html_text[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]

    def parse_card(self, block: str) -> dict | None:
        if self._SOLD_OUT_RE.search(block):
            return None          # sold out — price reads "Contact", skip entirely

        m = self._TITLE_RE.search(block)
        if not m:
            return None          # a card we cannot identify is skipped, not guessed
        href, raw_name = m.groups()
        name = self._clean_name(raw_name)
        price = self._extract_price(block)
        if not name or not price:
            return None          # "Contact" or 0: no price to report

        return {
            "name": name,
            "price_pkr": price,
            "url": self._resolve_url(href),
            "category": "",   # filled in by caller
            "source": SOURCE,
            "scraped_at": self.now(),
            "thumbnail_url": self._extract_thumb(block),
        }

    def extract_total(self, html_text: str) -> int | None:
        m = self._TOTAL_RE.search(html_text)
        return int(m.group(1).replace(",", "")) if m else None

    def next_page_url(self, url: str, page: int) -> str:
        return f"{url}?page={page}" if page > 1 else url

    @classmethod
    def _clean_name(cls, raw: str) -> str:
        # Two unescapes: one for the HTML attribute, then the one the API
        # path applies to WordPress's already-encoded name ("&amp;amp;" -> "&",
        # "&amp;#8243;" -> "″").
        return cls._api_names.clean_name(html.unescape(html.unescape(raw)))

    @classmethod
    def _extract_price(cls, block: str) -> int | None:
        m = cls._PRICE_RE.search(block)
        if not m:
            return None
        digits = re.sub(r"[^\d]", "", m.group(1))
        return int(digits) if digits else None

    @classmethod
    def _extract_thumb(cls, block: str) -> str | None:
        m = cls._THUMB_RE.search(block)
        return cls._absolute(m.group(1)) if m else None

    @classmethod
    def _resolve_url(cls, href: str) -> str:
        """
        Stored zah URLs are the Store API permalinks:
        https://zahcomputers.pk/product/<slug>/. Cards link /product/<slug>
        with no trailing slash. source_id is _slug(url), so the bytes must
        match exactly or every part becomes a duplicate.
        """
        return cls._absolute(href).rstrip("/") + "/"

    @staticmethod
    def _absolute(href: str) -> str:
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return f"{BASE}{href}"
        return f"{BASE}/{href}"


def main():
    """Standalone smoke test — does NOT write to DB."""
    run_listing_cli(ZahHtmlScraper, CATEGORIES, category_url, write_db=False, default_filter="gpu")


if __name__ == "__main__":
    main()
