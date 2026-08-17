"""
G1: Database() used to run _apply_schema()/_migrate() unconditionally on
every construction, remote or local. Any ad hoc script, notebook, or REPL
session that called Database() with no explicit target would silently apply
schema/column changes to hosted production Turso — the mechanism behind
production's unplanned drift (latest_price, delisted_at,
idx_parts_cat_active_price all landed outside any intended migration
window).

The guard is three-way, not two-way. The first cut only had "migrate" and
"raise", which took down every legitimate remote reader that had no reason
to migrate anything — the API server (backend/deps.py) and the weekly
Discord notifier both construct a Database against Turso in normal
operation and would have hard-failed on their first DB call. A caller that
passes allow_remote_migrations=False has declared it does not own the
schema; it connects and never runs DDL.

No test here touches a remote database. `_remote_migration_mode` is the pure
decision function `_apply_schema()` consults before executing a schema
statement against a remote target; testing it directly exercises the real
logic without opening a network connection. A full `Database(<turso url>)`
construction is intentionally not exercised here — that path is separately
blocked in test processes by the pre-existing PYTEST_CURRENT_TEST guard in
Database.__init__ (see conftest.py), which fires first and is out of scope.
"""
import pytest

from db.database import (
    Database,
    _ALLOW_REMOTE_MIGRATIONS_ENV,
    _refuse_remote_migration,
    _remote_migration_mode,
    get_db,
)

_FAKE_REMOTE_TARGET = "libsql://ppc-azaan-faisal606.aws-ap-northeast-1.turso.io"


def test_no_signal_from_anyone_refuses(monkeypatch):
    monkeypatch.delenv(_ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)
    assert _remote_migration_mode(None) == "refuse"


def test_explicit_true_runs_migrations(monkeypatch):
    monkeypatch.delenv(_ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)
    assert _remote_migration_mode(True) == "run"


def test_explicit_false_skips_rather_than_refusing(monkeypatch):
    """
    The API server and the Discord notifier pass False. They must CONNECT,
    not raise — refusing there is a total outage for a schema they were
    never going to change.
    """
    monkeypatch.delenv(_ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)
    assert _remote_migration_mode(False) == "skip"


def test_env_var_opts_in_when_kwarg_omitted(monkeypatch):
    monkeypatch.setenv(_ALLOW_REMOTE_MIGRATIONS_ENV, "1")
    assert _remote_migration_mode(None) == "run"


def test_kwarg_false_overrides_the_env_var(monkeypatch):
    """scripts/migrations/_guard.py sets the env var process-wide; a reader
    constructed afterwards in the same process must still not migrate."""
    monkeypatch.setenv(_ALLOW_REMOTE_MIGRATIONS_ENV, "1")
    assert _remote_migration_mode(False) == "skip"


def test_env_var_only_counts_when_exactly_1(monkeypatch):
    monkeypatch.setenv(_ALLOW_REMOTE_MIGRATIONS_ENV, "true")
    assert _remote_migration_mode(None) == "refuse"


def test_refusal_names_the_target_and_both_opt_in_mechanisms():
    with pytest.raises(RuntimeError) as exc:
        _refuse_remote_migration(_FAKE_REMOTE_TARGET)
    msg = str(exc.value)
    assert _FAKE_REMOTE_TARGET in msg
    assert "allow_remote_migrations" in msg
    assert _ALLOW_REMOTE_MIGRATIONS_ENV in msg


def test_local_sqlite_construction_unaffected(tmp_path, monkeypatch):
    # No allow_remote_migrations passed, no ALLOW_REMOTE_MIGRATIONS set — a
    # local SQLite target must still construct and migrate freely.
    monkeypatch.delenv(_ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)
    db = Database(tmp_path / "t.db")
    try:
        assert db._remote is False
        # It really did migrate: a table only _migrate()/_apply_schema()
        # creates is present.
        db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()
    finally:
        db.close()


def test_local_sqlite_migrates_even_when_the_caller_says_skip(tmp_path):
    """
    "skip" is a REMOTE-only decision. A local file is the caller's own
    scratch DB and is created empty — skipping schema there would hand back
    a connection with no tables.
    """
    db = Database(tmp_path / "t.db", allow_remote_migrations=False)
    try:
        db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()
    finally:
        db.close()


def test_get_db_forwards_the_setting(tmp_path):
    with get_db(tmp_path / "t.db", allow_remote_migrations=True) as db:
        assert db._remote_migration_mode == "run"
