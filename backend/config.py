import os
from pathlib import Path

# Load repo-root .env (local dev). override=False so real environment vars
# (CI GitHub secrets, Render env) always take precedence over the file.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# Local SQLite path — used only when Turso env vars are NOT set (dev/offline).
DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).parent.parent / "data" / "ppc.db")))

# Hosted Turso (libSQL) database. When TURSO_DATABASE_URL is set, the DB layer
# connects to Turso and DB_PATH is ignored. Leave unset to use local SQLite.
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")


def use_remote_db() -> bool:
    """True when the app should talk to Turso instead of the local SQLite file."""
    return bool(os.getenv("TURSO_DATABASE_URL"))
