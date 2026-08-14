"""
Shared skeleton for the paginated listing scrapers.

The listing scrapers previously carried their own copy of: the pagination
loop, the consecutive-failure bound, the total-count extraction, the
out-of-stock/card-splitting boilerplate and a main() smoke test. The copies
had drifted — some broke on the first page error and silently returned
whatever they had, some tolerated three failures before doing the same, and
four had byte-identical "of X results" extractors. Every bug found in one
had to be fixed in the others by hand, which is how several of them survived
this long (see docs/audit/scrapers.md).

Subclasses supply the site-specific parts via four hooks and nothing else:
    card_blocks(html)   -> the raw HTML broken into one string per product
    parse_card(block)   -> one card's HTML -> a product dict, or None to skip
    extract_total(html) -> the "N products" count on page 1, or None
    next_page_url(url, page) -> the URL for page N

`scrape(url)` is the template method: it drives fetch/pagination/bounds and
calls the hooks above. `_parse_page(html)` is kept as a public seam (not just
`card_blocks`+`parse_card` inlined) because it is the unit the parser test
suite pins per retailer — every subclass gets it for free from `card_blocks`
and `parse_card`, but a subclass with an irregular page shape can still
override it directly instead of forcing an awkward `card_blocks` split.
"""
import os
import sys
from collections import Counter
from typing import Iterable, Optional

from scrapers.base_scraper import BaseScraper
from scrapers.exceptions import ScrapeIncomplete


class ListingScraper(BaseScraper):
    MAX_PAGES = 200
    MAX_CONSECUTIVE_FAILURES = 3
    # Not read by scrape() itself — inter-page spacing is already covered by
    # BaseScraper.fetch()'s per-host _pace(). Kept as a documented config
    # surface (some subclasses previously had their own, inconsistent value
    # here — 1.0 for czone, 1.2 everywhere else — despite both doing nothing
    # beyond what fetch() already did on every call).
    PAGE_DELAY = 1.2

    # Set by each subclass to the value it stamps on every product's
    # "source" field — also used to label ScrapeIncomplete messages so a
    # failure log line names the retailer without a caller having to know it.
    SOURCE = ""

    # ---- subclass hooks -------------------------------------------------
    def card_blocks(self, html: str) -> Iterable[str]:
        raise NotImplementedError

    def parse_card(self, block: str) -> Optional[dict]:
        """Return a product dict, or None to skip an unidentifiable card."""
        raise NotImplementedError

    def extract_total(self, html: str) -> Optional[int]:
        return None

    def next_page_url(self, url: str, page: int) -> str:
        return f"{url}?page={page}" if page > 1 else url

    # ---- shared page parse, built from the two hooks above ---------------
    def _parse_page(self, html: str) -> list[dict]:
        results = []
        for block in self.card_blocks(html):
            item = self.parse_card(block)
            if item is not None:
                results.append(item)
        return results

    # ---- template method ------------------------------------------------
    def scrape(self, url: str) -> list[dict]:
        products: list[dict] = []
        seen: set[str] = set()
        failures = 0
        total: Optional[int] = None

        for page in range(1, self.MAX_PAGES + 1):
            page_url = self.next_page_url(url, page)
            try:
                html = self.fetch(page_url)
                failures = 0
            except Exception as exc:
                failures += 1
                if failures >= self.MAX_CONSECUTIVE_FAILURES:
                    err = ScrapeIncomplete(
                        f"{self.SOURCE}: {failures} consecutive page failures at page {page}"
                    )
                    err.partial_results = products
                    raise err from exc
                continue

            if total is None:
                total = self.extract_total(html)

            new = 0
            for item in self._parse_page(html):
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                products.append(item)
                new += 1

            if new == 0:
                break                       # no new products: end of listing
            if total is not None and len(products) >= total:
                break
        else:
            # A page cap this generous getting hit at all means the site is
            # serving "new" content forever (or is broken) — trust nothing
            # collected so far rather than deliver a harvest of unknown
            # shape. Deliberate asymmetry with the failures raise above: no
            # .partial_results here.
            raise ScrapeIncomplete(f"{self.SOURCE}: hit MAX_PAGES={self.MAX_PAGES} without finishing")

        return products


# --------------------------------------------------------------------------
# Shared "of X results" total extractor — byte-identical across redtech,
# techarc, techmatched and zahcomputers before this refactor. Kept as a
# module-level function (not forced onto every subclass) since amdhouse,
# czone and pakbyte report totals differently or not at all.
# --------------------------------------------------------------------------
import re as _re


def total_from_results_text(html: str) -> Optional[int]:
    """Shared 'Showing 1-24 of 132 results' pattern (WooCommerce's default loop)."""
    m = _re.search(r"of\s+([\d,]+)\s+results", html, _re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None


# --------------------------------------------------------------------------
# Shared `python -m scrapers.<name>` entry point. Seven near-identical
# main()s previously differed only in: whether they write to the DB, which
# single category they smoke-test by default, and whether --all overrides
# that default. Both flavours are covered here.
# --------------------------------------------------------------------------

def run_and_persist(scraper: BaseScraper, resolved: list[tuple[str, str]], *, write_db: bool) -> None:
    """
    resolved: [(url, our_category), ...] already built by the caller — lets
    amdhouse pass its dynamically-probed category list through the same path
    as every scraper with a static CATEGORIES table.
    """
    all_results: list[dict] = []
    for url, category in resolved:
        print(f"\n[{category.upper()}] {url}")
        try:
            products = scraper.scrape(url)
        except ScrapeIncomplete as e:
            print(f"  INCOMPLETE: {e}")
            products = getattr(e, "partial_results", None) or []
        for p in products:
            p["category"] = category
        print(f"  => {len(products)} products")
        all_results.extend(products)

    if not all_results:
        print("No products scraped.")
        sys.exit(1)

    print(f"\nTotal: {len(all_results)} products")
    for cat, n in sorted(Counter(p["category"] for p in all_results).items()):
        print(f"  {cat:15s} {n}")

    if not write_db:
        print("\n--- sample products ---")
        for p in all_results[:3]:
            print(f"  name:  {p['name'][:80]}")
            print(f"  price: {p['price_pkr']}")
            print(f"  url:   {p['url']}")
            print(f"  thumb: {(p['thumbnail_url'] or '')[:80]}")
            print()
        print("(no DB write — standalone smoke test)")
        return

    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
    from db.database import get_db
    db_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "ppc.db"))
    with get_db(db_path) as db:
        inserted = db.upsert_products(all_results)
        print(f"\nDB: {inserted} price rows written to {db_path}")
        stats = db.stats()
        print(f"DB stats: {stats['total_parts']} total parts, {stats['total_price_rows']} price rows")


def run_listing_cli(
    scraper_cls,
    categories: list[tuple],
    url_for,
    *,
    write_db: bool = True,
    default_filter: Optional[str] = None,
) -> None:
    """
    categories: module-level CATEGORIES — (slug_or_path, our_category, ...) tuples.
    url_for: slug_or_path -> full category URL.
    write_db=False is the "standalone smoke test" flavour (pakbyte, redtech,
    techarc, techmatched): defaults to `default_filter`'s category alone
    unless the process was invoked with --all.
    """
    scraper = scraper_cls()
    cats = categories
    if not write_db:
        only_default = "--all" not in sys.argv
        cats = [c for c in categories if c[1] == default_filter] if only_default else categories
    resolved = [(url_for(c[0]), c[1]) for c in cats]
    run_and_persist(scraper, resolved, write_db=write_db)
