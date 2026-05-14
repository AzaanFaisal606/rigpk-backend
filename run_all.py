"""
Run all PPC scrapers and write results to the database.

Usage:
    python run_all.py                      # run all scrapers
    python run_all.py czone                # run only czone
    python run_all.py zah rbt              # run specific scrapers
    python run_all.py --notify             # desktop notification on finish
    python run_all.py --test               # run DB integrity checks after scraping
    python run_all.py --notify --test      # both

Available scrapers: czone, zah, amd, rbt, junaid, tech, pakbyte
"""

import subprocess
import sys
import time
from collections import Counter

from db.database import get_db
from scrapers.czone.all_scraper import CzoneAllScraper, CATEGORIES as CZONE_CATS, BASE as CZONE_BASE
from scrapers.zahcomputers.scraper import ZahComputersScraper, CATEGORIES as ZAH_CATS, BASE as ZAH_BASE
from scrapers.amdhouse.scraper import AmdHouseScraper, CATEGORIES as AMD_CATS, BASE as AMD_BASE
from scrapers.rbtechngames.scraper import RbTechNGamesScraper, CATEGORIES as RBT_CATS, BASE as RBT_BASE
from scrapers.junaidtech.scraper import JunaidTechScraper, CATEGORIES as JT_CATS, BASE as JT_BASE
from scrapers.techarc.scraper import TechArcScraper, CATEGORIES as TECH_CATS, BASE as TECH_BASE
from scrapers.pakbyte.scraper import PakByteScraper, CATEGORIES as PB_CATS, BASE as PB_BASE

DB_PATH = "data/ppc.db"


def run_czone() -> list[dict]:
    scraper = CzoneAllScraper()
    results = []
    for path, category in CZONE_CATS:
        url = f"{CZONE_BASE}{path}"
        print(f"\n  [czone/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    return results


def run_zah() -> list[dict]:
    scraper = ZahComputersScraper()
    results = []
    for slug, category in ZAH_CATS:
        url = f"{ZAH_BASE}/shop/?product_cat={slug}"
        print(f"\n  [zah/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    return results


def run_amd() -> list[dict]:
    from scrapers.amdhouse.scraper import _find_valid_categories
    scraper = AmdHouseScraper()
    results = []
    print("  Checking amdhouse categories...")
    valid = _find_valid_categories()
    for url, category in valid:
        print(f"\n  [amd/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    return results


def run_rbt() -> list[dict]:
    scraper = RbTechNGamesScraper()
    results = []
    for path, category in RBT_CATS:
        url = f"{RBT_BASE}/product-category/{path}/"
        print(f"\n  [rbt/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    return results


def run_junaid() -> list[dict]:
    scraper = JunaidTechScraper()
    results = []
    for path, category, cat_id in JT_CATS:
        url = f"{JT_BASE}{path}"
        print(f"\n  [junaid/{category.upper()}]")
        try:
            products = scraper.scrape(url, known_category_id=cat_id, category=category)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        results.extend(products)
    return results


def run_tech() -> list[dict]:
    scraper = TechArcScraper()
    results = []
    for slug, category in TECH_CATS:
        url = f"{TECH_BASE}/{slug}/"
        print(f"\n  [tech/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    return results


def run_pakbyte() -> list[dict]:
    scraper = PakByteScraper()
    results = []
    for slug, category in PB_CATS:
        url = f"{PB_BASE}/collections/{slug}"
        print(f"\n  [pakbyte/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    return results


SCRAPERS = {
    "czone":   ("czone.com.pk",       run_czone),
    "zah":     ("zahcomputers.pk",    run_zah),
    "amd":     ("amdhouse.pk",        run_amd),
    "rbt":     ("rbtechngames.com",   run_rbt),
    "junaid":  ("junaidtech.pk",      run_junaid),
    "tech":    ("techarc.pk",         run_tech),
    "pakbyte": ("pakbyte.pk",         run_pakbyte),
}


def main():
    args = sys.argv[1:]
    do_notify = "--notify" in args
    do_test   = "--test"   in args
    args = [a for a in args if a not in ("--notify", "--test")]

    to_run = {k: v for k, v in SCRAPERS.items() if not args or k in args}

    if not to_run:
        print(f"Unknown scraper(s): {args}. Available: {list(SCRAPERS)}")
        sys.exit(1)

    all_results: list[dict] = []
    t0 = time.time()

    for key, (label, fn) in to_run.items():
        print(f"\n{'='*60}")
        print(f"Running: {label}")
        print("="*60)
        try:
            results = fn()
            print(f"\n  => {len(results)} products from {label}")
            if not results:
                print(f"  WARNING: 0 products from {label} — scraper may have failed silently")
            all_results.extend(results)
        except Exception as e:
            print(f"  SCRAPER FAILED: {e}")

    if not all_results:
        print("\nNo products scraped across any site.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Total scraped: {len(all_results)} products in {time.time()-t0:.0f}s")
    counts = Counter(f"{p['source']} / {p['category']}" for p in all_results)
    for k, n in sorted(counts.items()):
        print(f"  {k:40s} {n}")

    with get_db(DB_PATH) as db:
        inserted = db.upsert_products(all_results)
        s = db.stats()
        print(f"\nDB: {inserted} new price rows written to {DB_PATH}")
        print(f"DB: {s['total_parts']} total parts, {s['total_price_rows']} total price rows")
        print(f"\nBy category:")
        for cat, n in sorted(s["by_category"].items()):
            print(f"  {cat:15s} {n}")
        print(f"\nBy source:")
        for src, n in sorted(s["by_source"].items()):
            print(f"  {src:30s} {n}")

    # Optional: DB integrity checks
    tests_ok = None
    if do_test:
        print(f"\n{'='*60}")
        print("Running DB integrity checks...")
        print("="*60)
        from tests.test_db_integrity import run as run_integrity
        tests_ok = run_integrity()

    # Optional: desktop notification
    if do_notify:
        scrapers_run = len(to_run)
        products_count = len(all_results)
        if tests_ok is None:
            db_status = ""
        elif tests_ok:
            db_status = " | [PASS] DB checks"
        else:
            db_status = " | [FAIL] DB checks — see terminal"
        body = f"{scrapers_run} scrapers | {products_count:,} products | {time.time()-t0:.0f}s{db_status}"
        subprocess.run(
            ["notify-send", "-a", "PPC Scraper", "PPC Scrape Complete", body],
            check=False,
        )


if __name__ == "__main__":
    main()
