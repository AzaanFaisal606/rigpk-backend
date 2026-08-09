"""
One-shot: create idx_price_log_latest on the target DB via a direct execute().

schema.sql declares this index, and it appears on a fresh local SQLite file,
but it is missing from production Turso — libSQL appears to skip
`CREATE INDEX ... DESC` inside executescript(). Issuing it as a standalone
statement works. Idempotent (IF NOT EXISTS) and verified by read-back.
"""
import sys

from db.database import get_db

DDL = (
    "CREATE INDEX IF NOT EXISTS idx_price_log_latest "
    "ON price_log(part_id, scraped_at DESC, id)"
)


def main() -> int:
    db = get_db()
    print(f"target: {db._target} (remote={db._remote})")
    before = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='price_log'"
    ).fetchall()}
    print("before:", sorted(before))

    db._conn.execute(DDL)
    db._conn.commit()

    after = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='price_log'"
    ).fetchall()}
    print("after: ", sorted(after))
    db.close()

    if "idx_price_log_latest" not in after:
        print("FAILED — index still absent after commit", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
