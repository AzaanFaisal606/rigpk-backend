"""
Backfill parts.latest_price from price_log.

Idempotent and safe to re-run: it recomputes every row from price_log, then
verifies zero divergence by read-back. Run once per environment (local, then
production Turso) after deploying the column.

Usage:
    python scripts/migrations/2026_08_14_latest_price_backfill.py
"""
import sys

from db.database import Database

BACKFILL = """
UPDATE parts SET latest_price = (
    SELECT price_pkr FROM price_log
    WHERE part_id = parts.id AND price_pkr IS NOT NULL
    ORDER BY scraped_at DESC, id DESC
    LIMIT 1
)
"""

VERIFY = """
SELECT COUNT(*) FROM parts p
WHERE p.latest_price IS NOT (
    SELECT price_pkr FROM price_log
    WHERE part_id = p.id AND price_pkr IS NOT NULL
    ORDER BY scraped_at DESC, id DESC
    LIMIT 1
)
"""


def main() -> int:
    db = Database()
    print(f"target: {db._target}")
    db._conn.execute(BACKFILL)
    db._conn.commit()

    filled = db._conn.execute(
        "SELECT COUNT(*) FROM parts WHERE latest_price IS NOT NULL"
    ).fetchone()[0]
    drift = db._conn.execute(VERIFY).fetchone()[0]
    total = db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
    db.close()

    print(f"parts: {total}  latest_price filled: {filled}  divergent: {drift}")
    if drift:
        print("FAILED — latest_price diverges from price_log", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
