"""
Production guard for one-shot migration scripts.

Every migration under scripts/migrations/ writes directly to whatever DB
`db.database.Database()` resolves to. That resolution is env-driven (see
CLAUDE.md "DB target is env-driven") and this repo's local `.env` sets
TURSO_DATABASE_URL, so a bare `python scripts/migrations/foo.py` targets
hosted production Turso by default unless something stops it first.

Call resolve_target() at the very top of a migration's main(), before any
Database() construction or write:

    from scripts.migrations._guard import resolve_target

    def main() -> int:
        resolve_target()
        db = Database()
        ...

It prints what it detected and raises SystemExit(1) if that's production
and the caller didn't pass --yes-production. Never prompts — these scripts
run non-interactively (CI, cron).
"""
import os
import sys
from urllib.parse import urlparse

# db.database triggers `load_dotenv(repo_root/.env)` (override=False) at
# import time. Importing it here — before reading os.environ — makes this
# guard see exactly the same TURSO_DATABASE_URL a bare `Database()` call
# would resolve to, including the .env fallback.
import db.database  # noqa: F401  (import for its load_dotenv side effect)

# Turso hostnames in this project are "<db-name>-<org-slug>.<region>.turso.io".
# Stripping the org slug off the leading label recovers the database name even
# when that name contains hyphens (ppc-refactor, ppc-restoretest, ...).
# Production moved accounts in Sep 2026 (libsql://ppc-backup-azaanfaisal...),
# which changed both the org slug and the database name.
_ORG_SUFFIXES = ("-azaan-faisal606", "-azaanfaisal")
_PRODUCTION_DB_NAMES = frozenset({"ppc", "ppc-backup"})


def _db_name_from_url(url: str) -> str:
    host = urlparse(url).hostname or url
    label = host.split(".")[0]
    for suffix in _ORG_SUFFIXES:
        if label.endswith(suffix):
            return label[: -len(suffix)]
    return label


def resolve_target(argv: list[str] | None = None) -> str:
    """Print + return the DB target this process would write to.

    Raises SystemExit(1) if the target is production Turso (database name in
    _PRODUCTION_DB_NAMES) and --yes-production is not present in argv.
    """
    if argv is None:
        argv = sys.argv[1:]

    url = os.getenv("TURSO_DATABASE_URL")
    if url:
        db_name = _db_name_from_url(url)
        is_production = db_name in _PRODUCTION_DB_NAMES
        target = url
    else:
        db_name = None
        is_production = False
        target = os.getenv(
            "DB_PATH",
            str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent
                / "data" / "ppc.db"),
        )

    print(f"target: {target}" + (f"  (db={db_name})" if db_name else " (local sqlite)"))

    if is_production and "--yes-production" not in argv:
        print(
            f"REFUSING: target resolves to production Turso database {db_name!r}. "
            "Re-run with --yes-production to proceed.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # A migration script reaching this line has already had its production
    # target explicitly confirmed above (or resolved to a non-production /
    # local target). That is exactly the confirmation db.database's remote
    # migration guard (ALLOW_REMOTE_MIGRATIONS) requires, so grant it here
    # rather than making every migration script set it by hand. setdefault
    # so an operator's own explicit env choice is never overridden.
    os.environ.setdefault("ALLOW_REMOTE_MIGRATIONS", "1")

    return target
