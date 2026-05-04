"""
Run all prebuilt scrapers and write results to DB.

Usage:
    python -m scrapers.prebuilts.run_prebuilts            # all sources
    python -m scrapers.prebuilts.run_prebuilts zestro     # single source
    python -m scrapers.prebuilts.run_prebuilts redtech techmatched
"""

import sys
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from scrapers.prebuilts.zestro.scraper import ZestroScraper
from scrapers.prebuilts.redtech.scraper import RedTechScraper
from scrapers.prebuilts.techmatched.scraper import TechMatchedScraper
from db.database import get_db

SCRAPERS = {
    "zestro":      ZestroScraper,
    "redtech":     RedTechScraper,
    "techmatched": TechMatchedScraper,
}


def main():
    requested = [a.lower() for a in sys.argv[1:]]
    targets = {k: v for k, v in SCRAPERS.items() if not requested or k in requested}

    if not targets:
        print(f"Unknown sources: {requested}. Valid: {list(SCRAPERS)}")
        sys.exit(1)

    db_path = os.path.join(ROOT, "data", "ppc.db")
    total_scraped = 0
    total_written = 0

    for name, cls in targets.items():
        print(f"\n=== {name.upper()} ===")
        try:
            scraper = cls()
            results = scraper.scrape_all()
            print(f"  => {len(results)} prebuilts scraped")
            if results:
                with get_db(db_path) as db:
                    n = db.upsert_prebuilts(results)
                print(f"  => {n} rows written to DB")
                total_scraped += len(results)
                total_written += n
            else:
                print(f"  WARNING: 0 prebuilts from {name}")
        except Exception as e:
            print(f"  ERROR scraping {name}: {e}")

    if total_scraped == 0:
        print("\nNo prebuilts scraped.")
        sys.exit(1)

    with get_db(db_path) as db:
        stats = db.prebuilt_stats()

    print(f"\n=== DONE ===")
    print(f"Total scraped:  {total_scraped}")
    print(f"DB rows written: {total_written}")
    print(f"DB prebuilt total: {stats['total']}")
    for source, count in stats["by_source"].items():
        print(f"  {source:30s} {count}")


if __name__ == "__main__":
    main()
