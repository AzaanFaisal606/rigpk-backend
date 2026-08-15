"""
G1: Database() used to run _apply_schema()/_migrate() unconditionally on
every construction, remote or local. Any ad hoc script, notebook, or REPL
session that called Database() with no explicit target would silently apply
schema/column changes to hosted production Turso — the mechanism behind
production's unplanned drift (latest_price, delisted_at,
idx_parts_cat_active_price all landed outside any intended migration
window).

No test here touches a remote database. `_check_remote_migration_allowed` is
the pure guard function `_apply_schema()` calls before ever executing a
schema statement against a remote target; testing it directly exercises the
real refusal/opt-in logic without opening a network connection. A full
`Database(<turso url>)` construction is intentionally not exercised here —
that path is separately blocked in test processes by the pre-existing
PYTEST_CURRENT_TEST guard in Database.__init__ (see conftest.py), which
fires first and is out of scope for this change.
"""
import pytest

from db.database import (
    Database,
    _ALLOW_REMOTE_MIGRATIONS_ENV,
    _check_remote_migration_allowed,
    get_db,
)

_FAKE_REMOTE_TARGET = "libsql://ppc-azaan-faisal606.aws-ap-northeast-1.turso.io"


def test_remote_without_optin_raises():
    with pytest.raises(RuntimeError) as exc:
        _check_remote_migration_allowed(_FAKE_REMOTE_TARGET, False)
    assert _FAKE_REMOTE_TARGET in str(exc.value)


def test_remote_with_optin_proceeds():
    # Must not raise.
    _check_remote_migration_allowed(_FAKE_REMOTE_TARGET, True)


def test_error_message_names_the_opt_in_mechanisms():
    with pytest.raises(RuntimeError) as exc:
        _check_remote_migration_allowed(_FAKE_REMOTE_TARGET, False)
    msg = str(exc.value)
    assert "allow_remote_migrations" in msg
    assert _ALLOW_REMOTE_MIGRATIONS_ENV in msg


def test_local_sqlite_construction_unaffected(tmp_path):
    # No allow_remote_migrations passed, no ALLOW_REMOTE_MIGRATIONS set — a
    # local SQLite target must still construct and migrate freely.
    db = Database(tmp_path / "t.db")
    try:
        assert db._remote is False
    finally:
        db.close()


def test_database_reads_env_var_when_kwarg_omitted(monkeypatch, tmp_path):
    monkeypatch.setenv(_ALLOW_REMOTE_MIGRATIONS_ENV, "1")
    db = Database(tmp_path / "t.db")
    try:
        assert db._allow_remote_migrations is True
    finally:
        db.close()


def test_database_kwarg_overrides_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv(_ALLOW_REMOTE_MIGRATIONS_ENV, "1")
    db = Database(tmp_path / "t.db", allow_remote_migrations=False)
    try:
        assert db._allow_remote_migrations is False
    finally:
        db.close()


def test_get_db_forwards_allow_remote_migrations(tmp_path):
    with get_db(tmp_path / "t.db", allow_remote_migrations=True) as db:
        assert db._allow_remote_migrations is True
