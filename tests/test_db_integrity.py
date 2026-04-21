"""
DB integrity checks for PPC.
Run standalone: python -m tests.test_db_integrity
Exits 0 if all checks pass, 1 if any fail.
"""
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "ppc.db"

EXPECTED_SOURCES = {
    "czone.com.pk", "zahcomputers.pk", "amdhouse.pk",
    "rbtechngames.com", "junaidtech.pk",
}

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = ""):
    global passed, failed
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    if ok:
        passed += 1
    else:
        failed += 1


def run():
    if not DB_PATH.exists():
        print(f"  [FAIL] DB not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 1. All 5 sources present with > 0 parts
    sources_in_db = {
        r[0] for r in conn.execute("SELECT DISTINCT source FROM parts").fetchall()
    }
    missing = EXPECTED_SOURCES - sources_in_db
    check(
        "All 5 sources present",
        len(missing) == 0,
        f"missing: {missing}" if missing else f"{len(sources_in_db)} sources found",
    )

    # 2. Each source has parts
    for source in sorted(EXPECTED_SOURCES):
        n = conn.execute(
            "SELECT COUNT(*) FROM parts WHERE source = ?", (source,)
        ).fetchone()[0]
        check(f"  {source} has parts", n > 0, f"{n} parts")

    # 3. Recent scrape — each source scraped in last 24h
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    for source in sorted(EXPECTED_SOURCES):
        n = conn.execute(
            """
            SELECT COUNT(*) FROM price_log pl
            JOIN parts p ON p.id = pl.part_id
            WHERE p.source = ? AND pl.scraped_at >= ?
            """,
            (source, cutoff),
        ).fetchone()[0]
        check(f"  {source} scraped in last 24h", n > 0, f"{n} new price rows")

    # 4. Thumbnail coverage per source (allow amdhouse/rbtechngames lower — no thumbs on site)
    LOW_THUMB_OK = {"amdhouse.pk", "rbtechngames.com"}
    for source in sorted(EXPECTED_SOURCES):
        total, with_thumb = conn.execute(
            """
            SELECT COUNT(*), SUM(CASE WHEN thumbnail_url IS NOT NULL THEN 1 ELSE 0 END)
            FROM parts WHERE source = ?
            """,
            (source,),
        ).fetchone()
        if total == 0:
            continue
        pct = (with_thumb or 0) / total * 100
        threshold = 10 if source in LOW_THUMB_OK else 70
        check(
            f"  {source} thumbnails",
            pct >= threshold,
            f"{pct:.0f}% ({with_thumb}/{total})",
        )

    # 5. Overall spec coverage > 80%
    total, with_specs = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN specs IS NOT NULL THEN 1 ELSE 0 END) FROM parts"
    ).fetchone()
    pct = (with_specs or 0) / total * 100 if total else 0
    check("Spec coverage > 80%", pct >= 80, f"{pct:.1f}% ({with_specs}/{total})")

    # 6. Price sanity — no prices below 100 or above 2,000,000
    outliers = conn.execute(
        "SELECT COUNT(*) FROM price_log WHERE price_pkr IS NOT NULL AND (price_pkr < 100 OR price_pkr > 2000000)"
    ).fetchone()[0]
    check("Price sanity (100–2,000,000 PKR)", outliers == 0, f"{outliers} outliers")

    # 7. No orphan price_log rows
    orphans = conn.execute(
        "SELECT COUNT(*) FROM price_log WHERE part_id NOT IN (SELECT id FROM parts)"
    ).fetchone()[0]
    check("No orphan price_log rows", orphans == 0, f"{orphans} orphans")

    # 8. Category distribution — at least 5 distinct categories
    cats = conn.execute("SELECT COUNT(DISTINCT category) FROM parts").fetchone()[0]
    check("≥5 distinct categories", cats >= 5, f"{cats} categories")

    conn.close()

    total_checks = passed + failed
    print(f"\n  {passed}/{total_checks} checks passed")
    return failed == 0


if __name__ == "__main__":
    print("\nDB Integrity Checks")
    print("=" * 40)
    ok = run()
    sys.exit(0 if ok else 1)
