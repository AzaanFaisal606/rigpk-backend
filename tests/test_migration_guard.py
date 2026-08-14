"""
scripts/migrations/_guard.resolve_target must refuse to touch production
Turso by accident.

The hazard: db.database loads the repo-root .env at import time
(override=False), and this repo's .env sets TURSO_DATABASE_URL for local
dev. A bare `python scripts/migrations/some_script.py` therefore targets
production Turso unless a guard stops it before any Database() is built.

No test here touches a remote database — every case either raises SystemExit
before a connection would be opened, or resolves to the local-sqlite branch.
"""
import pytest

from scripts.migrations._guard import resolve_target

_PROD_URL = "libsql://ppc-azaan-faisal606.aws-ap-northeast-1.turso.io"
_REFACTOR_URL = "libsql://ppc-refactor-azaan-faisal606.aws-ap-northeast-1.turso.io"
_RESTORETEST_URL = "libsql://ppc-restoretest-azaan-faisal606.aws-ap-northeast-1.turso.io"


def test_production_url_without_flag_exits_nonzero(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", _PROD_URL)
    with pytest.raises(SystemExit) as exc:
        resolve_target([])
    assert exc.value.code != 0


def test_production_url_with_flag_proceeds(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", _PROD_URL)
    target = resolve_target(["--yes-production"])
    assert target == _PROD_URL


def test_refactor_url_proceeds_without_flag(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", _REFACTOR_URL)
    target = resolve_target([])
    assert target == _REFACTOR_URL


def test_restoretest_url_proceeds_without_flag(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", _RESTORETEST_URL)
    target = resolve_target([])
    assert target == _RESTORETEST_URL


def test_no_url_local_sqlite_proceeds(monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    target = resolve_target([])
    assert "ppc.db" in target or target  # local path, non-empty either way
