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

# Turso hostnames in this project are "<db-name>-<org-slug>.<region>.turso.io"
# (see CLAUDE.md: libsql://ppc-azaan-faisal606.aws-ap-northeast-1.turso.io).
# The org slug is fixed, so stripping it off the leading label recovers the
# actual database name even when that name itself contains hyphens
# (ppc-refactor, ppc-restoretest, ...).
_ORG_SUFFIX = "-azaan-faisal606"
_PRODUCTION_DB_NAME = "ppc"


def _db_name_from_url(url: str) -> str:
    host = urlparse(url).hostname or url
    label = host.split(".")[0]
    if label.endswith(_ORG_SUFFIX):
        return label[: -len(_ORG_SUFFIX)]
    return label


def resolve_target(argv: list[str] | None = None) -> str:
    """Print + return the DB target this process would write to.

    Raises SystemExit(1) if the target is production Turso (database name
    exactly "ppc") and --yes-production is not present in argv.
    """
    if argv is None:
        argv = sys.argv[1:]

    url = os.getenv("TURSO_DATABASE_URL")
    if url:
        db_name = _db_name_from_url(url)
        is_production = db_name == _PRODUCTION_DB_NAME
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

    return target
