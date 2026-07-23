"""
Shared pytest fixtures / safety guards.

Tests must ALWAYS run against isolated local SQLite (a tmp file), never the
hosted Turso DB. Since `db.database` auto-loads the repo-root `.env` on import,
`TURSO_DATABASE_URL` would otherwise be set and force every `Database(tmp_path)`
in the suite to the remote production DB — some tests write, and
`test_trends` calls `rebuild_price_trends()` which DELETEs and rewrites the
`price_trends` table. That would corrupt production.

This autouse fixture strips the Turso vars for the duration of every test, so
`Database()` always takes the local-SQLite branch regardless of `.env` or a
polluted shell environment.
"""
import pytest


@pytest.fixture(autouse=True)
def _force_local_sqlite(monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
