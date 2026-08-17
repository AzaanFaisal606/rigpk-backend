"""
One-shot: populate parts.name_norm / prebuilts.name_norm for rows written
before the column existed.

Idempotent — only touches rows where name_norm IS NULL, so it is safe to
re-run and safe to run against a DB that is already fully backfilled.

Local:  python -m scripts.migrations.2026_08_07_name_norm_backfill
Turso:  TURSO_DATABASE_URL / TURSO_AUTH_TOKEN set (from .env) -> same command.
"""
import sys

from db.database import get_db
from db.tokenize import normalize_name
from scripts.migrations._guard import resolve_target

# Each batch is ONE statement, so it costs one network round trip rather than
# one per row. That distinction is everything against remote Turso: an
# executemany() of 500 UPDATEs is 500 round trips, which measured at roughly
# 136 rows/minute — over an hour for the ~10.7k rows here. Batched as a single
# CASE the same work is a few dozen round trips.
#
# 200 rows = 600 bound parameters (2 per CASE arm + 1 per IN entry), comfortably
# under SQLite's default 999-variable limit.
BATCH = 200


def backfill(db, table: str) -> int:
    rows = db._conn.execute(
        f"SELECT id, name FROM {table} WHERE name_norm IS NULL"
    ).fetchall()
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        arms = " ".join("WHEN ? THEN ?" for _ in chunk)
        holes = ",".join("?" for _ in chunk)
        params: list = []
        for r in chunk:
            params.extend([r[0], normalize_name(r[1])])
        params.extend([r[0] for r in chunk])
        db._conn.execute(
            f"UPDATE {table} SET name_norm = CASE id {arms} END WHERE id IN ({holes})",
            params,
        )
        db._conn.commit()
        print(f"  {table}: {min(i + BATCH, len(rows))}/{len(rows)}", flush=True)
    return len(rows)


def main() -> int:
    # Prints the resolved target, refuses production without --yes-production,
    # and sets ALLOW_REMOTE_MIGRATIONS once the target is confirmed. CLAUDE.md
    # documents this script as re-runnable against the live DB, so it needs the
    # opt-in the same way every other migration here does.
    resolve_target()
    db = get_db()
    print(f"target: {db._target} (remote={db._remote})", flush=True)
    total = sum(backfill(db, t) for t in ("parts", "prebuilts"))
    for table in ("parts", "prebuilts"):
        left = db._conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE name_norm IS NULL"
        ).fetchone()[0]
        print(f"verify {table}: {left} rows still NULL")
        if left:
            print("FAILED — rows remain unpopulated", file=sys.stderr)
            return 1
    db.close()
    print(f"backfilled {total} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
