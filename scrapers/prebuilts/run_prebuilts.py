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

from datetime import datetime, timezone

from scrapers.base_scraper import blocked_hosts, reset_host_state
from scrapers.prebuilts.zestro.scraper import ZestroScraper, SOURCE as ZESTRO_SOURCE
from scrapers.prebuilts.redtech.scraper import RedTechScraper, SOURCE as REDTECH_SOURCE
from scrapers.prebuilts.techmatched.scraper import TechMatchedScraper, SOURCE as TM_SOURCE
from db.database import backup_db, get_db

SCRAPERS = {
    "zestro":      (ZESTRO_SOURCE,  ZestroScraper),
    "redtech":     (REDTECH_SOURCE, RedTechScraper),
    "techmatched": (TM_SOURCE,      TechMatchedScraper),
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

    backup = backup_db(db_path)
    if backup:
        print(f"DB: backup written to {backup}")

    reset_host_state()

    for name, (source, cls) in targets.items():
        print(f"\n=== {name.upper()} ===")
        started = datetime.now(timezone.utc).isoformat()
        blocked_before = blocked_hosts()
        results: list[dict] = []
        error: str | None = None
        try:
            scraper = cls()
            results = scraper.scrape_all()
            print(f"  => {len(results)} prebuilts scraped")
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            print(f"  ERROR scraping {name}: {error}")

        newly_blocked = blocked_hosts() - blocked_before
        if newly_blocked:
            error = error or f"host blocked mid-run: {', '.join(sorted(newly_blocked))}"
            print(f"  BLOCKED: {', '.join(sorted(newly_blocked))} stopped responding")

        # Same rule as the parts orchestrator: sweep only a run we trust, so a
        # blocked host keeps its existing prebuilts instead of losing them.
        ok = bool(results) and error is None
        if not ok and not results and error is None:
            error = "returned 0 prebuilts"
            print(f"  WARNING: 0 prebuilts from {name}")
        if not ok:
            print(f"  SKIP sweep for {name} — keeping existing rows (marked stale)")

        n = swept = 0
        with get_db(db_path) as db:
            if results:
                n = db.upsert_prebuilts(results)
                print(f"  => {n} rows written to DB")
            if ok:
                swept = db.deactivate_unseen_prebuilts(source)
                if swept:
                    print(f"  => {source}: {swept} prebuilts marked inactive")
            db.record_scrape_run(
                source, kind="prebuilt", started_at=started,
                products=len(results), ok=ok, swept=swept, error=error,
            )

        total_scraped += len(results)
        total_written += n

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
