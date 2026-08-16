"""
Operational DB integrity check — run against whatever `TURSO_DATABASE_URL`
(or local `DB_PATH`) currently points at.

Companion to tests/test_db_integrity.py, which checks schema/write-path
invariants against an isolated tmp DB and never touches the network. This
script instead carries the checks that are only meaningful against a real,
populated target: source coverage, recent-scrape freshness, and aggregate
data-quality ratios.

Read-only: never runs schema/migrations, so it opens the DB with
allow_remote_migrations=False and is safe to point at any target, including
production `ppc`. It still only *reads* — nothing here writes.

    set -a && . ./.env.refactor && set +a && python scripts/check_db_integrity.py

Caveat: the "scraped in last 24h" check fails legitimately against a stale
local copy (e.g. `data/ppc.db` pulled once and not re-scraped since) — that
is not a bug in the check, it means the target hasn't run a scrape recently.

Prints one PASS/FAIL line per check and exits 1 if any check failed.
"""
import sys
from datetime import datetime, timedelta, timezone

from backend.constants import VALID_SOURCES as EXPECTED_SOURCES
from db.database import Database

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    if ok:
        passed += 1
    else:
        failed += 1


def run(db: Database) -> bool:
    conn = db._conn

    # 1. All expected sources present
    sources_in_db = {
        r[0] for r in conn.execute("SELECT DISTINCT source FROM parts").fetchall()
    }
    missing = EXPECTED_SOURCES - sources_in_db
    check(
        "All expected sources present",
        len(missing) == 0,
        f"missing: {missing}" if missing else f"{len(sources_in_db)} sources found",
    )

    # 2. Each source has active parts
    for source in sorted(EXPECTED_SOURCES):
        n = conn.execute(
            "SELECT COUNT(*) FROM parts WHERE source = ? AND is_active = 1",
            (source,),
        ).fetchone()[0]
        check(f"  {source} has active parts", n > 0, f"{n} active parts")

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

    # 4. Thumbnail coverage per source (amdhouse/rbtechngames run lower — no
    # thumbnails on those sites)
    LOW_THUMB_OK = {"amdhouse.pk", "rbtechngames.com"}
    for source in sorted(EXPECTED_SOURCES):
        total, with_thumb = conn.execute(
            """
            SELECT COUNT(*), SUM(CASE WHEN thumbnail_url IS NOT NULL THEN 1 ELSE 0 END)
            FROM parts WHERE source = ? AND is_active = 1
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
        "SELECT COUNT(*), SUM(CASE WHEN specs IS NOT NULL THEN 1 ELSE 0 END) "
        "FROM parts WHERE is_active = 1"
    ).fetchone()
    pct = (with_specs or 0) / total * 100 if total else 0
    check("Spec coverage > 80%", pct >= 80, f"{pct:.1f}% ({with_specs}/{total})")

    # 6. Price sanity — no active-part prices below 100 or above 2,000,000
    outliers = conn.execute(
        """
        SELECT COUNT(*) FROM price_log pl
        JOIN parts p ON p.id = pl.part_id
        WHERE p.is_active = 1 AND pl.price_pkr IS NOT NULL
          AND (pl.price_pkr < 100 OR pl.price_pkr > 2000000)
        """
    ).fetchone()[0]
    check("Price sanity (100-2,000,000 PKR)", outliers == 0, f"{outliers} outliers")

    # 7. Category distribution — at least 5 distinct active categories
    cats = conn.execute(
        "SELECT COUNT(DISTINCT category) FROM parts WHERE is_active = 1"
    ).fetchone()[0]
    check("At least 5 distinct active categories", cats >= 5, f"{cats} categories")

    total_checks = passed + failed
    print(f"\n  {passed}/{total_checks} checks passed")
    return failed == 0


def main() -> int:
    db = Database(allow_remote_migrations=False)
    print("\nDB Integrity Checks")
    print(f"  target: {db._target}")
    print("=" * 40)
    try:
        ok = run(db)
    finally:
        db.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
