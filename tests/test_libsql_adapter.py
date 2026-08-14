"""
_Cursor._run retries transient libsql errors up to _MAX_ATTEMPTS, then must
raise — never fall off the end of the loop and return None. A bare
fall-through used to do exactly that, and every caller followed up with
`.fetchone()` on the None result, turning a transient network error into a
confusing `AttributeError: 'NoneType' object has no attribute 'fetchone'`.
"""
import pytest

from db import libsql_adapter
from db.libsql_adapter import _MAX_ATTEMPTS, _Cursor


class _AlwaysTransientCursor:
    """Fakes the underlying libsql cursor: every execute() looks like a
    transient network failure, never a constraint violation."""

    def __init__(self):
        self.calls = 0

    def execute(self, sql, params):
        self.calls += 1
        raise ValueError("connection reset by peer")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(libsql_adapter.time, "sleep", lambda *_: None)


def test_exhausted_retries_raise_not_return_none():
    fake = _AlwaysTransientCursor()
    cur = _Cursor(fake)

    with pytest.raises(ValueError, match="connection reset"):
        cur.execute("SELECT 1")

    assert fake.calls == _MAX_ATTEMPTS
