"""
pakbyte.pk scraper — Shopify products.json

How it works:
  - GET /collections/<slug>/products.json?limit=250&page=N, until a page
    returns fewer than 250 products.
  - In stock = any variant `available`. Price = the cheapest available
    variant (the listing showed the cheapest variant too).
  - URL is /products/<handle>, as the listing linked it.
  - Thumbnail is images[0].src, moved from cdn.shopify.com to the store's own
    /cdn/shop/ path, which is what the listing served (same file).
  - robots.txt disallows /collections/*sort_by*. Never add sort_by.

Usage:
    python -m scrapers.pakbyte.scraper          # GPU only (smoke test, no DB write)
    python -m scrapers.pakbyte.scraper --all    # all categories
"""

import json
import re
import urllib.parse
from typing import Optional

from scrapers.base_scraper import BaseScraper
from scrapers.exceptions import ScrapeIncomplete
from scrapers.listing_scraper import run_listing_cli

SOURCE = "pakbyte.pk"
BASE = "https://www.pakbyte.pk"

# (collection handle, our_category_key)
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

# https://cdn.shopify.com/s/files/1/0589/8049/9523/files/x.jpg?v=1
#   -> https://www.pakbyte.pk/cdn/shop/files/x.jpg?v=1
_SHOPIFY_CDN_RE = re.compile(r'^https?://cdn\.shopify\.com/s/files/(?:\d+/)+')


class PakByteScraper(BaseScraper):

    SOURCE = SOURCE
    CATEGORIES = CATEGORIES
    PER_PAGE = 250
    MAX_PAGES = 200

    def api_url(self, slug: str, page: int) -> str:
        query = urllib.parse.urlencode({"limit": self.PER_PAGE, "page": page})
        return f"{BASE}/collections/{slug}/products.json?{query}"

    def parse_item(self, item: dict) -> Optional[dict]:
        """One products.json product -> a product dict, or None to skip it."""
        available = [v for v in item.get("variants") or [] if v.get("available")]
        if not available:
            return None
        try:
            price = int(min(float(v["price"]) for v in available))
        except (KeyError, TypeError, ValueError):
            return None
        if price <= 0:
            return None
        handle = item.get("handle") or ""
        name = (item.get("title") or "").strip()
        if not handle or not name:
            return None
        images = item.get("images") or []
        src = images[0].get("src") if images else None
        return {
            "name": name,
            "price_pkr": price,
            "url": f"{BASE}/products/{handle}",
            "category": "",         # filled in by the run_all wrapper
            "source": SOURCE,
            "scraped_at": self.now(),
            "thumbnail_url": _SHOPIFY_CDN_RE.sub(f"{BASE}/cdn/shop/", src) if src else None,
        }

    def scrape(self, slug: str) -> list[dict]:
        """Same failure contract as WooStoreScraper.scrape()."""
        products: list[dict] = []
        seen: set[str] = set()
        for page in range(1, self.MAX_PAGES + 1):
            try:
                items = json.loads(self.fetch(self.api_url(slug, page)))["products"]
                if not isinstance(items, list):
                    raise ValueError(f"expected a list of products, got {type(items).__name__}")
            except ScrapeIncomplete as exc:
                exc.partial_results = products
                raise
            except Exception as exc:
                err = ScrapeIncomplete(f"{SOURCE}: {slug} page {page} failed: {type(exc).__name__}: {exc}")
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

        raise ScrapeIncomplete(f"{SOURCE}: {slug} hit MAX_PAGES={self.MAX_PAGES} without finishing")


def main():
    """Standalone smoke test — does NOT write to DB."""
    run_listing_cli(PakByteScraper, CATEGORIES, lambda slug: slug, write_db=False, default_filter="gpu")


if __name__ == "__main__":
    main()
