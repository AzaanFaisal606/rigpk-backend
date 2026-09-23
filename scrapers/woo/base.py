"""
WooCommerce Store API scraper, shared by the six WooCommerce retailers.

How it works:
  - GET {BASE}/wp-json/wc/store/v1/products?category=<slug>&per_page=100&page=N
    Public, no auth, same bot UA as everything else. `category` takes the
    product_cat slug and includes child categories. An unknown slug returns
    `[]` with a 200, not a 404.
  - Paginate until a page returns fewer than PER_PAGE items. The X-WP-Total
    headers exist, but fetch() returns the body only.
  - Stock and price come from JSON fields, not theme markup: skip
    `is_in_stock: false` and price <= 0 ("call for price").
  - Never send `stock_status=`: zah's robots.txt disallows it. Stock is
    filtered here, client-side, on every site.

A subclass sets SOURCE, BASE and CATEGORIES, and overrides clean_name() when
the API name carries text the shop page hides. See docs/api.md.
"""

import html as _html
import json
import urllib.parse
from typing import Optional

from scrapers.base_scraper import BaseScraper
from scrapers.exceptions import ScrapeIncomplete


class WooStoreScraper(BaseScraper):
    SOURCE = ""
    BASE = ""
    # (product_cat slug, our category). Several slugs may map to one category.
    CATEGORIES: list[tuple[str, str]] = []

    PER_PAGE = 100
    MAX_PAGES = 200
    # Which images[0] URL to store. "thumbnail" is the shop-card crop; a site
    # whose listing showed the full-size image uses "src" so stored URLs
    # don't churn.
    THUMB_KEY = "thumbnail"

    def api_url(self, slug: str, page: int) -> str:
        query = urllib.parse.urlencode({"category": slug, "per_page": self.PER_PAGE, "page": page})
        return f"{self.BASE}/wp-json/wc/store/v1/products?{query}"

    def clean_name(self, name: str) -> str:
        return name.strip()

    def parse_item(self, item: dict) -> Optional[dict]:
        """One Store API product -> a product dict, or None to skip it."""
        if not item.get("is_in_stock"):
            return None
        prices = item.get("prices") or {}
        try:
            price = int(prices["price"]) // 10 ** int(prices.get("currency_minor_unit") or 0)
        except (KeyError, TypeError, ValueError):
            return None
        if price <= 0:
            return None             # "call for price"
        url = item.get("permalink") or ""
        name = self.clean_name(_html.unescape(item.get("name") or ""))
        if not url or not name:
            return None
        images = item.get("images") or []
        thumbnail = images[0].get(self.THUMB_KEY) if images else None
        return {
            "name": name,
            "price_pkr": price,
            "url": url,
            "category": "",         # filled in by the run_all wrapper
            "source": self.SOURCE,
            "scraped_at": self.now(),
            "thumbnail_url": thumbnail or None,
        }

    def scrape(self, slug: str) -> list[dict]:
        """
        Every in-stock, priced product in one category. Never returns a
        truncated harvest as complete: a page that fails (after fetch()'s own
        retries) raises ScrapeIncomplete carrying what came before it on
        .partial_results. HostBlocked, itself a ScrapeIncomplete, propagates
        with the same attribute. A category that is empty on page 1 is not an
        error: a sold-out category is legitimate, and health.py judges the run.
        """
        products: list[dict] = []
        seen: set[str] = set()
        for page in range(1, self.MAX_PAGES + 1):
            try:
                items = json.loads(self.fetch(self.api_url(slug, page)))
                if not isinstance(items, list):
                    # An error object ({"code": ..., "message": ...}) or a
                    # challenge page that happens to parse. Not a page of
                    # products, and not an empty one either.
                    raise ValueError(f"expected a JSON list, got {type(items).__name__}")
            except ScrapeIncomplete as exc:
                exc.partial_results = products
                raise
            except Exception as exc:
                err = ScrapeIncomplete(f"{self.SOURCE}: {slug} page {page} failed: {type(exc).__name__}: {exc}")
                err.partial_results = products
                raise err from exc

            for item in items:
                row = self.parse_item(item)
                if row is None or row["url"] in seen:
                    continue
                seen.add(row["url"])
                products.append(row)

            if len(items) < self.PER_PAGE:
                return products

        # No partial results, same as ListingScraper: a store serving full
        # pages forever is broken, so trust nothing collected.
        raise ScrapeIncomplete(f"{self.SOURCE}: {slug} hit MAX_PAGES={self.MAX_PAGES} without finishing")

