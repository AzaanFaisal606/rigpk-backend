"""
Backfill part_details.

    python scripts/backfill_details.py --zah [--limit N]   # zah product pages, uncapped by default
    python scripts/backfill_details.py --reparse           # re-parse stored raw below PARSER_VERSION

--reparse needs no network: it rebuilds the parsed columns from the stored raw.
--zah fetches one page per active zah part that has no details yet.

The target DB is printed first. `.env` points at production Turso, so a
production target is refused unless --yes-production is passed.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import get_db  # noqa: E402
from scrapers.details import PARSER_VERSION  # noqa: E402
from scrapers.zah.html.details import fetch_zah_details  # noqa: E402
from scripts.migrations._guard import resolve_target  # noqa: E402

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "ppc.db")
REPARSE_BATCH = 200


def _reparse(db) -> int:
    total = 0
    batch = []
    for item in db.iter_details_for_reparse(PARSER_VERSION):
        batch.append(item)
        if len(batch) >= REPARSE_BATCH:
            total += db.write_details(batch)
            db._conn.commit()
            batch = []
    if batch:
        total += db.write_details(batch)
        db._conn.commit()
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zah", action="store_true", help="fetch missing zah product pages")
    ap.add_argument("--limit", type=int, default=None, help="cap for --zah (default: no cap)")
    ap.add_argument("--reparse", action="store_true", help="re-parse rows below the current parser version")
    ap.add_argument("--yes-production", action="store_true", help="allow a production Turso target")
    args = ap.parse_args(argv)
    if not (args.zah or args.reparse):
        ap.error("pass --zah and/or --reparse")

    resolve_target(argv if argv is not None else sys.argv[1:])
    with get_db(DB_PATH, allow_remote_migrations=True) as db:
        if args.reparse:
            print(f"reparsed {_reparse(db)} row(s)")
        if args.zah:
            print(fetch_zah_details(db, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
