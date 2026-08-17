"""
The remote-migration guard's first cut was two-way: migrate, or raise. That
took the API down.

`ThreadSafeDatabase._connect_if_needed()` is the single place the whole API
acquires a connection, and it built `Database(self._path)` with no opt-in.
Against Turso — which is every deployed run, and every local
`uvicorn main:app --reload` picking up this repo's own `.env` — that raised
on the first DB call and on every reconnect after it, because the
once-per-process `_REMOTE_SCHEMA_APPLIED` cache is only populated on the
success path. A total outage from a guard meant to prevent schema drift.

The correct answer is neither "migrate" nor "raise": the API is a caller
that does not own the schema, so it declares that and connects without
running any DDL. These tests pin both halves of that — the flag reaches the
constructor, and it is False and not True.

Entirely offline: `Database` is patched out, so no target is ever resolved
and no connection is ever opened.
"""
import backend.deps as deps
from backend.deps import ThreadSafeDatabase


def test_api_connect_declares_it_does_not_own_the_schema(seeded_db, monkeypatch):
    seen = {}

    class _Fake:
        def ping(self):
            return "ok"

    def _capture(path, **kwargs):
        seen["path"] = path
        seen.update(kwargs)
        return _Fake()

    monkeypatch.setattr(deps, "Database", _capture)

    wrapper = ThreadSafeDatabase(seeded_db)
    # Force the lazy connect through the real _connect_if_needed path.
    assert wrapper.ping() == "ok"

    assert "allow_remote_migrations" in seen, (
        "the API opened a Database with no explicit stance on remote "
        "migrations — against Turso that is a hard RuntimeError on the "
        "first request"
    )
    assert seen["allow_remote_migrations"] is False, (
        "the API must not migrate a remote DB; True would reinstate the "
        "exact drift the guard exists to stop"
    )
