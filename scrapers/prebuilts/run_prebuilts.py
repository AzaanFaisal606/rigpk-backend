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

from scrapers import health
from scrapers.base_scraper import blocked_hosts, reset_host_state
from scrapers.exceptions import ScrapeIncomplete
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
    argv = sys.argv[1:]
    do_strict = "--strict" in argv   # CI: exit non-zero on any anomaly → self-heal
    requested = [a.lower() for a in argv if a != "--strict"]
    targets = {k: v for k, v in SCRAPERS.items() if not requested or k in requested}

    if not targets:
        print(f"Unknown sources: {requested}. Valid: {list(SCRAPERS)}")
        sys.exit(1)

    db_path = os.path.join(ROOT, "data", "ppc.db")
    total_scraped = 0
    total_written = 0
    anomalies: list[str] = []

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
        except ScrapeIncomplete as e:
            # Same rule as run_all.py: the scraper still collected some
            # prebuilts before choking — keep them for upsert below, but
            # `error` being set forces `ok = False` so the sweep is skipped.
            results = getattr(e, "partial_results", None) or []
            error = f"{type(e).__name__}: {e}"
            print(f"  INCOMPLETE: {error} — keeping {len(results)} prebuilt(s) collected before the fault")
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
        with get_db(db_path, allow_remote_migrations=True) as db:
            # Active count for this source before/after — the scrape report's
            # before → after columns. before captured pre-upsert.
            before_n = db.prebuilt_stats()["by_source"].get(source, 0)
            if results:
                n = db.upsert_prebuilts(results)
                print(f"  => {n} rows written to DB")
            if ok:
                swept = db.deactivate_unseen_prebuilts(source)
                if swept:
                    print(f"  => {source}: {swept} prebuilts marked inactive")
            after_n = db.prebuilt_stats()["by_source"].get(source, 0)
            db.record_scrape_run(
                source, kind="prebuilt", started_at=started,
                products=len(results), ok=ok, swept=swept,
                before_active=before_n, after_active=after_n, error=error,
            )

        # Prebuilt sources are far smaller than part sources (redtech has only 13
        # rows total) — the part-scraper's floor of 20 would exempt them from
        # ever being flagged, so use the lower prebuilt-specific floor instead.
        anomalies += health.source_anomalies(
            source, before_n, after_n, ok, error,
            floor=health.MIN_PREBUILT_SOURCE_BASELINE,
        )

        total_scraped += len(results)
        total_written += n

    if total_scraped == 0:
        print("\nNo prebuilts scraped.")
        sys.exit(1)

    with get_db(db_path, allow_remote_migrations=True) as db:
        stats = db.prebuilt_stats()

    print(f"\n=== DONE ===")
    print(f"Total scraped:  {total_scraped}")
    print(f"DB rows written: {total_written}")
    print(f"DB prebuilt total: {stats['total']}")
    for source, count in stats["by_source"].items():
        print(f"  {source:30s} {count}")

    if anomalies:
        print(f"\nANOMALIES DETECTED ({len(anomalies)}):")
        for a in anomalies:
            print(f"  ✗ {a}")
        if do_strict:
            print(f"\n--strict: exiting non-zero on {len(anomalies)} anomaly(ies).")
            sys.exit(1)
    else:
        print("\nHealth check: no anomalies.")


if __name__ == "__main__":
    main()
