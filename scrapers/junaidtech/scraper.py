"""
junaidtech.pk scraper — webx.pk platform (Nuxt SSR + JSON API)

How it works:
  - Fetches the SSR page once to extract a short-lived Bearer token and
    the category's internal ID from the embedded __NUXT_DATA__ payload.
  - Then calls POST /api/ProductListing/GetProductListingV2 with startRow
    pagination (up to 100 results per request) to get all products as JSON.
  - Token expires in ~15 min; it's re-fetched per scrape run.

Usage:
    python -m scrapers.junaidtech.scraper
"""

import json
import os
import re
import sys
import time
import urllib.request

from scrapers.base_scraper import BaseScraper

SOURCE = "junaidtech.pk"
BASE   = "https://www.junaidtech.pk"
API    = "https://frontapi.mywebx.pk/api/ProductListing/GetProductListingV2"

PAGE_SIZE  = 100
PAGE_DELAY = 1.5

# (url_path, our_category, categoryID)
# categoryIDs extracted from __NUXT_DATA__ on each sub-category page
CATEGORIES: list[tuple[str, str, str]] = [
    ("/graphics-cards-gpu",  "gpu",         "3403011"),
    ("/processors-cpu",      "cpu",         "3403009"),
    ("/motherboards",        "motherboard", "3403012"),
    ("/ram-memory",          "ram",         "3403013"),
    ("/solid-state-drives-ssd", "ssd",       "3403020"),
    ("/hard-drives-hdd",     "hdd",         "3403019"),
    ("/power-supplies-psu",  "psu",         "3403014"),
    ("/pc-casings",          "case",        "3403010"),
    ("/cooling-solutions",   "cooling",     "3403236"),
    ("/monitors-led-tv",     "monitor",     "814041"),
]


class JunaidTechScraper(BaseScraper):

    def scrape(self, url: str, known_category_id: str | None = None, category: str = "") -> list[dict]:
        """
        url is the category page URL, used to fetch a fresh Bearer token.
        known_category_id: hardcoded categoryID to pass to the API.
        category: our category label to stamp on each product dict.
        Returns all products via the JSON API.
        """
        print(f"    Fetching SSR page for Bearer token...")
        html = self.fetch(url)
        token, extracted_id = self._extract_auth(html)
        category_id = known_category_id or extracted_id
        if not token or not category_id:
            raise RuntimeError("Could not extract auth token or category ID from SSR page")

        all_products: list[dict] = []
        seen_ids: set[str] = set()
        start_row = 1

        while True:
            print(f"    API rows {start_row}–{start_row + PAGE_SIZE - 1}...")
            try:
                batch, total = self._api_fetch(token, category_id, start_row, PAGE_SIZE, category)
            except Exception as e:
                print(f"    API error at row {start_row}: {e} — stopping.")
                break

            if not batch:
                break

            new = [p for p in batch if p["url"] not in seen_ids]
            for p in new:
                seen_ids.add(p["url"])
            all_products.extend(new)

            print(f"    {len(new)} new (total so far: {len(all_products)} / {total})")

            if len(all_products) >= total:
                print(f"    collected all {total} — done.")
                break

            start_row += PAGE_SIZE
            time.sleep(PAGE_DELAY)

        return all_products

    def _api_fetch(self, token: str, category_id: str, start_row: int, results: int, category: str = "") -> tuple[list[dict], int]:
        scraped_at = self.now()
        s3 = "https://static.webx.pk/files"

        body = json.dumps({
            "keyword": "", "categoryID": category_id, "collectionID": "0",
            "brands": "", "variants": "", "searchFields": "", "priceRange": "",
            "sortBy": "2", "startRow": str(start_row), "results": str(results),
            "stockStatus": "",
        }).encode()

        req = urllib.request.Request(
            API,
            data=body,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "Referer": f"{BASE}/",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())

        data = d.get("data", {})
        total = int(data.get("totalRecords", 0))
        items = data.get("results", [])

        products = []
        for item in items:
            name = (item.get("productName") or "").strip()
            if not name:
                continue

            slug = item.get("productURL", "")
            product_url = f"{BASE}/{slug}" if slug and not slug.startswith("http") else slug

            price_raw = item.get("productPrice", {})
            price_pkr = None
            if isinstance(price_raw, dict):
                raw = price_raw.get("price") or price_raw.get("salePrice") or price_raw.get("regularPrice")
                if raw:
                    try:
                        price_pkr = int(float(raw))
                    except (ValueError, TypeError):
                        pass
            elif isinstance(price_raw, (int, float)):
                price_pkr = int(price_raw)

            thumb_path = item.get("thumbnailFile") or item.get("imageFile") or ""
            if thumb_path and not thumb_path.startswith("http"):
                thumb_path = f"{s3}/{thumb_path}"
            thumbnail = thumb_path or None

            products.append({
                "name": name,
                "price_pkr": price_pkr,
                "url": product_url,
                "category": category,
                "source": SOURCE,
                "scraped_at": scraped_at,
                "thumbnail_url": thumbnail,
            })

        return products, total

    @staticmethod
    def _extract_auth(html: str) -> tuple[str | None, str | None]:
        """Extract Bearer token and pc-components category ID from __NUXT_DATA__."""
        data_tag = re.search(
            r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not data_tag:
            return None, None

        arr = json.loads(data_tag.group(1))

        # Token is in arr[4] -> tokens -> accessToken (all references by index)
        try:
            store_obj = arr[4]           # {status, message, data, tokens}
            tokens_idx = store_obj["tokens"]
            token_obj = arr[tokens_idx]  # {accessToken, refreshToken}
            access_token = arr[token_obj["accessToken"]]
        except Exception:
            access_token = None

        # Category ID: the url-type key holds {item_id, slug, ...}
        category_id = None
        try:
            state_obj = arr[3]  # top-level state dict
            url_type_idx = state_obj.get("url-type-/pc-components")
            if url_type_idx is not None:
                url_type = arr[url_type_idx]
                category_id = str(arr[url_type["item_id"]] if isinstance(url_type["item_id"], int) else url_type["item_id"])
        except Exception:
            pass

        # Fallback: scan for item_id matching known pattern
        if not category_id:
            m = re.search(r'"item_id"\s*:\s*"(\d+)"', html)
            if m:
                category_id = m.group(1)

        return access_token, category_id

def main():
    scraper = JunaidTechScraper()
    all_results: list[dict] = []

    for path, category, cat_id in CATEGORIES:
        url = f"{BASE}{path}"
        print(f"\n[{category.upper()}] {url}")
        products = scraper.scrape(url, known_category_id=cat_id, category=category)
        print(f"  => {len(products)} products")
        all_results.extend(products)

    if not all_results:
        print("No products scraped.")
        sys.exit(1)

    from collections import Counter
    counts = Counter(p["category"] for p in all_results)
    print(f"\nTotal: {len(all_results)} products")
    for cat, n in sorted(counts.items()):
        print(f"  {cat:15s} {n}")

    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from db.database import get_db
    db_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "ppc.db"))
    with get_db(db_path) as db:
        inserted = db.upsert_products(all_results)
        print(f"\nDB: {inserted} price rows written")
        s = db.stats()
        print(f"DB stats: {s['total_parts']} parts, {s['total_price_rows']} price rows")


if __name__ == "__main__":
    main()
