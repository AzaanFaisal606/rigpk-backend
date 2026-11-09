"""
czone.com.pk — webx.pk platform (Nuxt SSR + JSON listing API), same as junaidtech.

czone moved to webx in late Oct 2026. The SSR listing pages still render, but
`?page=N` now shifts the window by one product instead of one page, so the old
HTML pager saw ~1/3 of each category and reported success (the sweep then hid
the rest). The HTML scraper is backed up in backups/czone-scraper-20261109/.

How it works, per category:
  - GET the SSR category page once: __NUXT_DATA__ carries a short-lived
    anonymous Bearer token and the category's internal id (`item_id` under
    the `url-type-<path>` key).
  - POST frontapi.mywebx.pk/api/ProductListing/GetProductListingV2 with
    startRow paging (100/request), no server-side stock filter, so
    `totalRecords` is the whole category and completeness is checkable.
  - Keep `instock` and `onorderonly`, drop `outofstock` (the HTML scraper only
    dropped "Out Of Stock" cards, so the catalogue keeps the same meaning).

`productURL` is byte-identical to the old card `href`, so source_ids and price
history carry over.

Usage:
    python -m scrapers.czone.scraper [--all]     # no DB write unless asked
"""

import json
import time  # kept for tests that monkeypatch this module's `time.sleep`

from scrapers.base_scraper import BaseScraper
from scrapers.details.parse import webx_raw
from scrapers.exceptions import ScrapeIncomplete
from scrapers.junaidtech.scraper import JunaidTechScraper

SOURCE = "czone.com.pk"
BASE = "https://www.czone.com.pk"
API = "https://frontapi.mywebx.pk/api/ProductListing/GetProductListingV2"

PAGE_SIZE = 100
PAGE_DELAY = 1.0
MAX_PAGES = 50          # czone's biggest category is a few hundred rows; pure insurance

# Stock states we list. Anything else (outofstock, or a state webx adds later)
# is skipped, never guessed.
KEEP_STOCK = frozenset({"instock", "onorderonly"})

# Category path (also the Nuxt `url-type-<path>` key) → our category key
CATEGORIES: list[tuple[str, str]] = [
    ("/graphic-cards-pakistan-ppt.154.aspx",          "gpu"),
    ("/processors-pakistan-ppt.85.aspx",              "cpu"),
    ("/motherboards-pakistan-ppt.157.aspx",           "motherboard"),
    ("/memory-module-ram-pakistan-ppt.127.aspx",      "ram"),
    ("/solid-state-drives-ssd-pakistan-ppt.263.aspx", "ssd"),
    ("/hard-drives-pakistan-ppt.93.aspx",             "hdd"),
    ("/power-supply-pakistan-ppt.183.aspx",           "psu"),
    ("/casing-pakistan-ppt.168.aspx",                 "case"),
    ("/cooling-solutions-pakistan-ppt.141.aspx",      "cooling"),
    ("/lcd-led-monitors-pakistan-ppt.108.aspx",       "monitor"),
]


class CzoneAllScraper(BaseScraper):

    SOURCE = SOURCE

    def scrape(self, url: str) -> list[dict]:
        """
        `url` is the category page. Returns every listed (non-sold-out) product
        with `category` left "" for the caller to fill. Raises ScrapeIncomplete
        on anything short of the full category: a run that saw part of a
        listing must never reach the freshness sweep as `ok`.
        """
        html = self.fetch(url)
        path = url[len(BASE):] if url.startswith(BASE) else url
        token = self.extract_token(html)
        category_id = self.extract_category_id(html, path)
        if not token or not category_id:
            raise ScrapeIncomplete(
                f"{SOURCE}: no {'token' if not token else 'category id'} in __NUXT_DATA__ of {url}"
            )

        products: list[dict] = []
        seen: set[str] = set()
        rows_seen = 0
        total = None
        start_row = 1
        for _ in range(MAX_PAGES):
            try:
                items, total = self.fetch_page(token, category_id, start_row)
            except Exception as exc:
                err = ScrapeIncomplete(f"{SOURCE}: listing API failed at row {start_row}: {exc}")
                err.partial_results = products
                raise err from exc
            if not items:
                break
            rows_seen += len(items)
            for item in items:
                p = self.parse_item(item)
                if p and p["url"] not in seen:
                    seen.add(p["url"])
                    products.append(p)
            if rows_seen >= total:
                break
            start_row += len(items)   # not PAGE_SIZE: a server-side cap must not skip rows
            time.sleep(PAGE_DELAY)

        if total is None or rows_seen < total:
            err = ScrapeIncomplete(
                f"{SOURCE}: saw {rows_seen} of {total} listing rows for {path}"
            )
            err.partial_results = products
            raise err
        return products

    def fetch_page(self, token: str, category_id: str, start_row: int) -> tuple[list[dict], int]:
        body = json.dumps({
            "keyword": "", "categoryID": category_id, "collectionID": "0",
            "brands": "", "variants": "", "searchFields": "", "priceRange": "",
            "sortBy": "2", "startRow": str(start_row), "results": str(PAGE_SIZE),
            "stockStatus": "",   # no server filter: totalRecords must cover every row
        }).encode()
        raw = self.fetch(API, headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": f"{BASE}/",
            "Authorization": f"Bearer {token}",
        }, data=body)
        d = json.loads(raw)
        if d.get("status") not in (None, "success"):
            raise ValueError(f"API status {d.get('status')!r}: {d.get('message')!r}")
        data = d.get("data") or {}
        return data.get("results") or [], int(data.get("totalRecords") or 0)

    def parse_item(self, item: dict) -> dict | None:
        stock = (item.get("productStock") or {}).get("stockStatus")
        if stock not in KEEP_STOCK:
            return None
        name = (item.get("productName") or "").strip()
        href = item.get("productURL") or ""
        if not name or not href:
            return None          # a row we cannot identify is skipped, not guessed

        price = item.get("productPrice") or {}
        price_pkr = None
        if str(price.get("isHidePrice", "0")) != "1":
            try:
                price_pkr = int(float(price.get("price") or 0)) or None
            except (TypeError, ValueError):
                price_pkr = None

        return {
            "name": name,
            "price_pkr": price_pkr,
            # Never re-encode: source_id is _slug(url) (CLAUDE.md rules).
            "url": href if href.startswith("http") else f"{BASE}{href if href.startswith('/') else '/' + href}",
            "category": "",   # filled in by caller
            "source": SOURCE,
            "scraped_at": self.now(),
            "thumbnail_url": item.get("thumbnailFile") or item.get("imageFile") or None,
            "details_raw": webx_raw(item, SOURCE),
        }

    @staticmethod
    def extract_token(html: str) -> str | None:
        return JunaidTechScraper._extract_token(html)

    @staticmethod
    def extract_category_id(html: str, path: str) -> str | None:
        arr = JunaidTechScraper._parse_nuxt_data(html)
        if arr is None:
            return None
        try:
            state = next(x for x in arr[:8] if isinstance(x, dict) and f"url-type-{path}" in x)
            ref = arr[state[f"url-type-{path}"]]["item_id"]
            val = arr[ref] if isinstance(ref, int) else ref
            return str(val) if str(val).isdigit() else None
        except (StopIteration, KeyError, IndexError, TypeError):
            return None


def main():
    import sys
    from collections import Counter
    cats = CATEGORIES if "--all" in sys.argv else CATEGORIES[:1]
    s = CzoneAllScraper()
    total = []
    for path, cat in cats:
        rows = s.scrape(f"{BASE}{path}")
        for r in rows:
            r["category"] = cat
        print(f"[{cat}] {len(rows)}")
        total.extend(rows)
    print(f"Total: {len(total)}  {dict(Counter(r['category'] for r in total))}")


if __name__ == "__main__":
    main()
