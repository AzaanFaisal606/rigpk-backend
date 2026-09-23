"""
Cross-check the DB against each retailer's own stock and price feed.

The listing parsers read HTML, and a theme change breaks them silently: pakbyte
listed 646 sold-out products for months, and techmatched stored the crossed-out
price of every sale item. The retailers publish the same facts as JSON. The 6
WooCommerce sites have the Store API (`/wp-json/wc/store/v1/products`) and
pakbyte has Shopify's `products.json`. This compares active rows against that
feed and flags a source whose parser has drifted.

Read-only on the DB (one `SELECT` per source over its active rows). Paced
through the scrapers' own `fetch()`, so the same UA and backoff apply.

    python scripts/crosscheck_stock.py                 # all sources
    python scripts/crosscheck_stock.py pakbyte.pk      # one
    python scripts/crosscheck_stock.py --strict        # exit 1 on drift

junaid and czone run on webx and have no public feed. They are skipped, and so
is a source whose last scrape failed: its rows are stale by design, not drift.
"""
import json
import os
import sys
from dataclasses import dataclass, field

from db.database import _slug, get_db
from scrapers.base_scraper import BaseScraper

WOO_SOURCES = {
    "amdhouse.pk":      "https://amdhouse.pk",
    "rbtechngames.com": "https://rbtechngames.com",
    "redtech.pk":       "https://redtech.pk",
    "techarc.pk":       "https://techarc.pk",
    "techmatched.pk":   "https://techmatched.pk",
    "zahcomputers.pk":  "https://zahcomputers.pk",
}
SHOPIFY_SOURCES = {"pakbyte.pk": "https://www.pakbyte.pk"}

MAX_PAGES = 200
# Drift thresholds, as a share of the active rows the feed also lists.
# A parser bug hits a slice (techmatched's sale items were 14 of 536, 2.6%),
# so the price limit is low, with a floor so a few repricings since the last
# scrape don't trip it.
SOLD_OUT_LIMIT = 0.05
PRICE_LIMIT = 0.02
MIN_ROWS = 5
PRICE_TOLERANCE = 0.01      # a price within 1% counts as the same


@dataclass
class Report:
    source: str
    active: int = 0
    matched: int = 0
    sold_out: list = field(default_factory=list)          # (source_id, db price)
    price_off: list = field(default_factory=list)         # (source_id, db price, feed price)

    @property
    def drift(self) -> list[str]:
        out = []
        if len(self.sold_out) >= MIN_ROWS and len(self.sold_out) / self.matched > SOLD_OUT_LIMIT:
            out.append(f"{len(self.sold_out)}/{self.matched} active rows are sold out on the site")
        in_stock = self.matched - len(self.sold_out)
        if len(self.price_off) >= MIN_ROWS and len(self.price_off) / in_stock > PRICE_LIMIT:
            out.append(f"{len(self.price_off)}/{in_stock} in-stock rows have a different price")
        return out


def compare(source: str, db_rows: dict[str, int], feed: dict[str, tuple[bool, int | None]]) -> Report:
    """db_rows: source_id -> latest_price. feed: source_id -> (in_stock, price)."""
    r = Report(source=source, active=len(db_rows))
    for sid, db_price in db_rows.items():
        if sid not in feed:
            continue
        r.matched += 1
        in_stock, price = feed[sid]
        if not in_stock:
            r.sold_out.append((sid, db_price))
        elif price and db_price and abs(db_price - price) / price > PRICE_TOLERANCE:
            r.price_off.append((sid, db_price, price))
    return r


class _Fetcher(BaseScraper):
    def scrape(self, url: str):
        del url
        return []


def _woo_feed(fetcher: _Fetcher, base: str) -> dict[str, tuple[bool, int | None]]:
    feed = {}
    for page in range(1, MAX_PAGES + 1):
        items = json.loads(fetcher.fetch(f"{base}/wp-json/wc/store/v1/products?per_page=100&page={page}"))
        if not items:
            break
        for p in items:
            prices = p.get("prices") or {}
            unit = 10 ** int(prices.get("currency_minor_unit", 0))
            price = int(prices["price"]) // unit if prices.get("price") else None
            try:
                feed[_slug(p["permalink"])] = (bool(p.get("is_in_stock")), price)
            except (KeyError, ValueError):
                continue
    return feed


def _shopify_feed(fetcher: _Fetcher, base: str) -> dict[str, tuple[bool, int | None]]:
    feed = {}
    for page in range(1, MAX_PAGES + 1):
        items = json.loads(fetcher.fetch(f"{base}/products.json?limit=250&page={page}"))["products"]
        if not items:
            break
        for p in items:
            variants = p.get("variants") or []
            in_stock = any(v.get("available") for v in variants)
            prices = [float(v["price"]) for v in variants if v.get("price")]
            feed[_slug(f"{base}/products/{p['handle']}")] = (in_stock, int(min(prices)) if prices else None)
    return feed


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    sources = {**WOO_SOURCES, **SHOPIFY_SOURCES}
    if wanted:
        sources = {s: b for s, b in sources.items() if s in wanted}

    fetcher = _Fetcher()
    drifted = []
    target = os.environ.get("DB_PATH")
    with (get_db(target, allow_remote_migrations=False) if target
          else get_db(allow_remote_migrations=False)) as db:
        health = db.source_health("parts")
        for source, base in sources.items():
            if health.get(source, {}).get("stale"):
                print(f"{source}: skipped, last scrape failed so its rows are stale")
                continue
            rows = db._conn.execute(
                "SELECT source_id, latest_price FROM parts WHERE source = ? AND is_active = 1",
                (source,),
            ).fetchall()
            try:
                feed = (_shopify_feed if source in SHOPIFY_SOURCES else _woo_feed)(fetcher, base)
            except Exception as e:
                print(f"{source}: feed unavailable ({type(e).__name__}: {e})")
                continue
            r = compare(source, {row[0]: row[1] for row in rows}, feed)
            print(f"{source}: {r.active} active, {r.matched} in feed, "
                  f"{len(r.sold_out)} sold out, {len(r.price_off)} price off")
            for sid, db_p, feed_p in r.price_off[:5]:
                print(f"    price  {sid}: db {db_p} vs site {feed_p}")
            for sid, db_p in r.sold_out[:5]:
                print(f"    soldout {sid}: db {db_p}")
            if r.drift:
                drifted.append(source)
                for line in r.drift:
                    print(f"  DRIFT {source}: {line}")
    return 1 if strict and drifted else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
