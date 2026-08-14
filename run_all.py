"""
Run all PPC scrapers and write results to the database.

Usage:
    python run_all.py                      # run all scrapers
    python run_all.py czone                # run only czone
    python run_all.py zah rbt              # run specific scrapers
    python run_all.py --notify             # desktop notification on finish
    python run_all.py --test               # run DB integrity checks after scraping
    python run_all.py --notify --test      # both

Available scrapers: czone, zah, amd, rbt, junaid, tech, pakbyte, redtech, techmatched
"""

import io
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from db.database import backup_db, get_db
from scrapers import health
from scrapers.base_scraper import host_blocked, reset_host_state
from scrapers.exceptions import ScrapeIncomplete
from scrapers.czone.all_scraper import CzoneAllScraper, CATEGORIES as CZONE_CATS, BASE as CZONE_BASE
from scrapers.zahcomputers.scraper import ZahComputersScraper, CATEGORIES as ZAH_CATS, BASE as ZAH_BASE
from scrapers.amdhouse.scraper import AmdHouseScraper, CATEGORIES as AMD_CATS, BASE as AMD_BASE
from scrapers.rbtechngames.scraper import RbTechNGamesScraper, CATEGORIES as RBT_CATS, BASE as RBT_BASE
from scrapers.junaidtech.scraper import JunaidTechScraper, CATEGORIES as JT_CATS, BASE as JT_BASE
from scrapers.techarc.scraper import TechArcScraper, CATEGORIES as TECH_CATS, BASE as TECH_BASE
from scrapers.pakbyte.scraper import PakByteScraper, CATEGORIES as PB_CATS, BASE as PB_BASE
from scrapers.redtech.scraper import RedTechScraper, CATEGORIES as RT_CATS, BASE as RT_BASE
from scrapers.techmatched.scraper import TechMatchedScraper, CATEGORIES as TM_CATS, BASE as TM_BASE

# Absolute, not "data/ppc.db": a cron job or CI runner invoking this from
# another directory would otherwise silently create and populate an empty DB
# next to whatever its cwd happened to be.
DB_PATH = str(Path(__file__).resolve().parent / "data" / "ppc.db")


def run_czone() -> list[dict]:
    scraper = CzoneAllScraper()
    results = []
    incomplete: list[str] = []
    for path, category in CZONE_CATS:
        url = f"{CZONE_BASE}{path}"
        print(f"\n  [czone/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except ScrapeIncomplete as e:
            # This category could not be fully seen — the OTHER categories in
            # this loop may still succeed below, and their products must not
            # be thrown away just because one category choked (that is the
            # bug this whole exception exists to prevent). Keep collecting,
            # then raise once every category has had its turn, carrying
            # everything gathered so far on .partial_results.
            print(f"    INCOMPLETE: {e}")
            incomplete.append(f"{category}: {e}")
            # scrape()'s own MAX_CONSECUTIVE_FAILURES raise carries forward
            # whatever pages it collected before choking (.partial_results) —
            # don't drop that here just because the category as a whole
            # didn't finish.
            partial = getattr(e, "partial_results", None) or []
            for p in partial:
                p["category"] = category
            results.extend(partial)
            continue
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    if incomplete:
        exc = ScrapeIncomplete(f"czone: {'; '.join(incomplete)}")
        exc.partial_results = results
        raise exc
    return results


def run_zah() -> list[dict]:
    scraper = ZahComputersScraper()
    results = []
    incomplete: list[str] = []
    for slug, category in ZAH_CATS:
        url = f"{ZAH_BASE}/shop/?product_cat={slug}"
        print(f"\n  [zah/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except ScrapeIncomplete as e:
            print(f"    INCOMPLETE: {e}")
            incomplete.append(f"{category}: {e}")
            continue
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    if incomplete:
        exc = ScrapeIncomplete(f"zah: {'; '.join(incomplete)}")
        exc.partial_results = results
        raise exc
    return results


def run_amd() -> list[dict]:
    from scrapers.amdhouse.scraper import _find_valid_categories
    scraper = AmdHouseScraper()
    results = []
    incomplete: list[str] = []
    print("  Checking amdhouse categories...")
    try:
        valid = _find_valid_categories()
    except ScrapeIncomplete as e:
        # Some category probes hit a real error (not a 404) — the categories
        # that DID resolve are still worth scraping, we just can't call this
        # source's coverage complete.
        print(f"  INCOMPLETE probing categories: {e}")
        valid = getattr(e, "valid_categories", [])
        incomplete.append(str(e))
    for url, category in valid:
        print(f"\n  [amd/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except ScrapeIncomplete as e:
            print(f"    INCOMPLETE: {e}")
            incomplete.append(f"{category}: {e}")
            continue
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    if incomplete:
        exc = ScrapeIncomplete(f"amd: {'; '.join(incomplete)}")
        exc.partial_results = results
        raise exc
    return results


def run_rbt() -> list[dict]:
    scraper = RbTechNGamesScraper()
    results = []
    incomplete: list[str] = []
    for path, category in RBT_CATS:
        url = f"{RBT_BASE}/product-category/{path}/"
        print(f"\n  [rbt/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except ScrapeIncomplete as e:
            print(f"    INCOMPLETE: {e}")
            incomplete.append(f"{category}: {e}")
            continue
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    if incomplete:
        exc = ScrapeIncomplete(f"rbt: {'; '.join(incomplete)}")
        exc.partial_results = results
        raise exc
    return results


def run_junaid() -> list[dict]:
    scraper = JunaidTechScraper()
    results = []
    incomplete: list[str] = []
    for path, category, cat_id in JT_CATS:
        url = f"{JT_BASE}{path}"
        print(f"\n  [junaid/{category.upper()}]")
        try:
            products = scraper.scrape(url, known_category_id=cat_id, category=category)
        except ScrapeIncomplete as e:
            print(f"    INCOMPLETE: {e}")
            incomplete.append(f"{category}: {e}")
            continue
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        results.extend(products)
    if incomplete:
        exc = ScrapeIncomplete(f"junaid: {'; '.join(incomplete)}")
        exc.partial_results = results
        raise exc
    return results


def run_tech() -> list[dict]:
    scraper = TechArcScraper()
    results = []
    incomplete: list[str] = []
    for slug, category in TECH_CATS:
        url = f"{TECH_BASE}/{slug}/"
        print(f"\n  [tech/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except ScrapeIncomplete as e:
            print(f"    INCOMPLETE: {e}")
            incomplete.append(f"{category}: {e}")
            continue
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    if incomplete:
        exc = ScrapeIncomplete(f"tech: {'; '.join(incomplete)}")
        exc.partial_results = results
        raise exc
    return results


def run_pakbyte() -> list[dict]:
    scraper = PakByteScraper()
    results = []
    incomplete: list[str] = []
    for slug, category in PB_CATS:
        url = f"{PB_BASE}/collections/{slug}"
        print(f"\n  [pakbyte/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except ScrapeIncomplete as e:
            print(f"    INCOMPLETE: {e}")
            incomplete.append(f"{category}: {e}")
            continue
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    if incomplete:
        exc = ScrapeIncomplete(f"pakbyte: {'; '.join(incomplete)}")
        exc.partial_results = results
        raise exc
    return results


def run_redtech() -> list[dict]:
    scraper = RedTechScraper()
    results = []
    incomplete: list[str] = []
    for slug, category in RT_CATS:
        url = f"{RT_BASE}/product-category/{slug}/"
        print(f"\n  [redtech/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except ScrapeIncomplete as e:
            print(f"    INCOMPLETE: {e}")
            incomplete.append(f"{category}: {e}")
            continue
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    if incomplete:
        exc = ScrapeIncomplete(f"redtech: {'; '.join(incomplete)}")
        exc.partial_results = results
        raise exc
    return results


def run_techmatched() -> list[dict]:
    scraper = TechMatchedScraper()
    results = []
    incomplete: list[str] = []
    for slug, category in TM_CATS:
        url = f"{TM_BASE}/product-category/{slug}/"
        print(f"\n  [techmatched/{category.upper()}]")
        try:
            products = scraper.scrape(url)
        except ScrapeIncomplete as e:
            print(f"    INCOMPLETE: {e}")
            incomplete.append(f"{category}: {e}")
            continue
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        for p in products:
            p["category"] = category
        results.extend(products)
    if incomplete:
        exc = ScrapeIncomplete(f"techmatched: {'; '.join(incomplete)}")
        exc.partial_results = results
        raise exc
    return results


SCRAPERS = {
    "czone":       ("czone.com.pk",       run_czone),
    "zah":         ("zahcomputers.pk",    run_zah),
    "amd":         ("amdhouse.pk",        run_amd),
    "rbt":         ("rbtechngames.com",   run_rbt),
    "junaid":      ("junaidtech.pk",      run_junaid),
    "tech":        ("techarc.pk",         run_tech),
    "pakbyte":     ("pakbyte.pk",         run_pakbyte),
    "redtech":     ("redtech.pk",         run_redtech),
    "techmatched": ("techmatched.pk",     run_techmatched),
}


def run_source(key: str, **_kwargs) -> dict:
    """
    Run one source end to end and report the outcome — no DB access here.

    Kept deliberately DB-free: run_sources() below calls this from a worker
    thread per source, and sqlite3 connections are not safe to share across
    threads. Rather than hand each thread its own Database (and coordinate
    writes from N threads), every worker only fetches/parses and returns
    products; run_all.main() does all DB work back on the main thread once
    every worker has finished, exactly like the old sequential loop did.
    """
    label, fn = SCRAPERS[key]
    print(f"\n{'='*60}")
    print(f"Running: {label}")
    print("="*60)
    started = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    error: str | None = None
    try:
        results = fn()
        print(f"\n  => {len(results)} products from {label}")
    except ScrapeIncomplete as e:
        # Some category(ies) inside this source could not be fully seen —
        # the run_X() functions still collect everything the OTHER
        # categories returned and carry it on .partial_results rather than
        # losing it. That partial list is what still gets upserted below;
        # only the sweep (gated on `ok`) is skipped.
        results = getattr(e, "partial_results", None) or []
        error = f"{type(e).__name__}: {e}"
        print(f"  INCOMPLETE: {error} — keeping {len(results)} product(s) collected before the fault")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        print(f"  SCRAPER FAILED: {error}")

    # Checked by this source's own host, not by diffing the global
    # blocked_hosts() set. Sources now run in parallel, each on its own
    # host, so a before/after diff of the whole breaker state could catch a
    # DIFFERENT source's host tripping in the same window and misattribute
    # that block to this source. host_blocked(label) only answers "did MY
    # host trip" — true only if THIS source's own fetch() calls did it.
    if host_blocked(label):
        error = error or f"host blocked mid-run: {label}"
        print(f"  BLOCKED: {label} stopped responding")

    # A run is trusted only if it finished cleanly AND returned something.
    # Partial data from a blocked host is still upserted (fresh prices are
    # worth keeping) but must not sweep — we can't tell "delisted" from
    # "never reached" on a host that cut us off.
    ok = bool(results) and error is None
    if not ok:
        if not results and error is None:
            error = "returned 0 products"
            print(f"  WARNING: 0 products from {label} — scraper may have failed silently")
        print(f"  SKIP sweep for {label} — keeping existing rows (marked stale)")

    return {
        "source": label, "started": started, "products": len(results),
        "ok": ok, "error": error, "results": results,
    }


class _PerSourceStdout:
    """
    Routes print() output from N concurrently-running sources into separate
    buffers, keyed by which source is currently running on the calling
    thread. Nine threads writing straight to stdout would interleave lines
    from different retailers mid-scrape and the log would stop reading like
    a coherent run; run_sources() flushes each source's buffer whole, in
    deterministic source order, once every worker has finished.
    """

    def __init__(self, real_stdout):
        self._real = real_stdout
        self._local = threading.local()
        self._buffers: dict[str, io.StringIO] = {}
        self._lock = threading.Lock()

    def start(self, source: str) -> None:
        self._local.source = source
        with self._lock:
            self._buffers[source] = io.StringIO()

    def write(self, s: str) -> None:
        source = getattr(self._local, "source", None)
        if source is None:
            # Nothing running on this thread claimed a source (e.g. the
            # main thread printing while the pool is up) — pass straight
            # through rather than dropping it.
            self._real.write(s)
            return
        with self._lock:
            self._buffers[source].write(s)

    def flush(self) -> None:
        pass  # buffered per-source; nothing to flush until pop()

    def pop(self, source: str) -> str:
        with self._lock:
            buf = self._buffers.pop(source, None)
        return buf.getvalue() if buf else ""


def run_sources(sources: list[str], **kwargs) -> list[dict]:
    """
    Scrape sources concurrently — one worker per host, never two per host.

    Every key in SCRAPERS maps to exactly one host, so one thread per
    requested source IS one thread per host: parallelism can never land two
    concurrent requests on the same retailer. Per-host pacing and the
    circuit breaker live inside BaseScraper.fetch()/_state() and are
    untouched — no retailer sees a request rate any different from a
    sequential run. The work is ~95% sleeping on network I/O, so threads are
    the right tool and the GIL is irrelevant.

    Results come back sorted into `sources` order regardless of completion
    order, so the Discord report and scrape_runs ordering do not depend on
    which host was slowest.
    """
    real_stdout = sys.stdout
    router = _PerSourceStdout(real_stdout)

    def _worker(source: str) -> dict:
        router.start(source)
        return run_source(source, **kwargs)

    results: dict[str, dict] = {}
    sys.stdout = router
    try:
        with ThreadPoolExecutor(max_workers=min(len(sources), 9)) as pool:
            futures = {pool.submit(_worker, s): s for s in sources}
            for fut, source in futures.items():
                try:
                    results[source] = fut.result()
                except Exception as exc:
                    results[source] = {
                        "source": source, "started": datetime.now(timezone.utc).isoformat(),
                        "products": 0, "ok": False, "error": str(exc), "results": [],
                    }
    finally:
        sys.stdout = real_stdout

    for s in sources:
        text = router.pop(s)
        if text:
            real_stdout.write(text)

    return [results[s] for s in sources]


def parse_sources(requested: list[str]) -> list[str]:
    """
    Resolve CLI/workflow source arguments against the registry.

    Empty input means "every source". An unrecognised name exits non-zero
    rather than silently scraping nothing — a run that scrapes nothing and
    exits 0 is indistinguishable from a successful run, and that result feeds
    the freshness sweep.
    """
    known = list(SCRAPERS)
    if not requested:
        return known
    unknown = [s for s in requested if s not in SCRAPERS]
    if unknown:
        raise SystemExit(
            f"Unknown source(s): {', '.join(unknown)}. Known: {', '.join(known)}"
        )
    return requested


def main():
    args = sys.argv[1:]
    do_notify = "--notify" in args
    do_test   = "--test"   in args
    do_strict = "--strict" in args   # CI: exit non-zero on any anomaly → self-heal
    args = [a for a in args if a not in ("--notify", "--test", "--strict")]

    sources = parse_sources(args)
    to_run = {k: v for k, v in SCRAPERS.items() if k in sources}

    all_results: list[dict] = []
    # One entry per source attempted, recorded to scrape_runs afterwards.
    # `ok` decides two things at once: whether the freshness sweep runs, and
    # whether the retailer shows a STALE ribbon on the landing page.
    runs: list[dict] = []
    t0 = time.time()
    reset_host_state()

    # One worker thread per requested source (== one per host, see
    # run_sources' docstring). All DB access stays below, on this thread,
    # once every worker has returned — run_source() never touches the DB.
    for r in run_sources(list(to_run.keys())):
        runs.append({k: v for k, v in r.items() if k != "results"})
        all_results.extend(r["results"])

    if not all_results:
        print("\nNo products scraped across any site.")
        with get_db(DB_PATH) as db:
            # Nothing scraped → nothing mutated, so before == after (rows kept).
            active = db.stats()["by_source"]
            for r in runs:
                n = active.get(r["source"], 0)
                db.record_scrape_run(
                    r["source"], kind="parts", started_at=r["started"],
                    products=r["products"], ok=False, swept=0,
                    before_active=n, after_active=n, error=r["error"],
                )
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Total scraped: {len(all_results)} products in {time.time()-t0:.0f}s")
    counts = Counter(f"{p['source']} / {p['category']}" for p in all_results)
    for k, n in sorted(counts.items()):
        print(f"  {k:40s} {n}")

    backup = backup_db(DB_PATH)
    if backup:
        print(f"\nDB: backup written to {backup}")

    with get_db(DB_PATH) as db:
        # Active counts before this run mutates anything — the "before" side of the
        # report and the health check. Captured pre-upsert so the sweep can't skew them.
        before_active = db.stats()["by_source"]
        before_cat = db.counts_by_source_category()

        inserted = db.upsert_products(all_results)

        # Per-retailer freshness sweep: parts not seen in this run go inactive
        # (hidden from the site) but keep their rows and price history. Sweeping
        # is whole-source on purpose — a retailer's listing should be entirely
        # from one run, never a mix of this run's rows and older leftovers.
        deactivated = 0
        swept_by_source: dict[str, int] = {}
        for r in runs:
            swept = db.deactivate_unseen_parts(r["source"]) if r["ok"] else 0
            swept_by_source[r["source"]] = swept
            deactivated += swept
            if swept:
                print(f"DB: {r['source']} — {swept} parts marked inactive (not seen this run)")

        # "After" side, once upsert + all sweeps have landed.
        after_active = db.stats()["by_source"]
        after_cat = db.counts_by_source_category()

        for r in runs:
            src = r["source"]
            db.record_scrape_run(
                src, kind="parts", started_at=r["started"],
                products=r["products"], ok=r["ok"], swept=swept_by_source[src],
                before_active=before_active.get(src, 0),
                after_active=after_active.get(src, 0),
                error=r["error"],
            )

        stale = [r["source"] for r in runs if not r["ok"]]
        if stale:
            print(f"DB: marked stale — {', '.join(stale)}")

        trend_rows = db.rebuild_price_trends()
        s = db.stats()
        print(f"\nDB: {inserted} new price rows written to {DB_PATH}")
        print(f"DB: {deactivated} parts marked inactive this run")
        print(f"DB: {trend_rows} price-trend rows computed")
        print(f"DB: {s['total_parts']} total parts, {s['total_price_rows']} total price rows")
        print(f"\nBy category:")
        for cat, n in sorted(s["by_category"].items()):
            print(f"  {cat:15s} {n}")
        print(f"\nBy source:")
        for src, n in sorted(s["by_source"].items()):
            print(f"  {src:30s} {n}")

        # Health check: stale sources, suspicious source-level drops, and
        # categories that had rows but scraped zero. Sensitive by design.
        anomalies = health.evaluate_parts(
            runs, before_active, after_active, before_cat, after_cat
        )

    if anomalies:
        print(f"\n{'='*60}")
        print(f"ANOMALIES DETECTED ({len(anomalies)}) — scrape needs a look:")
        print("="*60)
        for a in anomalies:
            print(f"  ✗ {a}")
    else:
        print("\nHealth check: no anomalies.")

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

    # --strict (CI): any anomaly fails the job so the self-heal workflow fires.
    # Fresh data was still written above — this only flips the exit code.
    if do_strict and anomalies:
        print(f"\n--strict: exiting non-zero on {len(anomalies)} anomaly(ies).")
        sys.exit(1)


if __name__ == "__main__":
    main()
