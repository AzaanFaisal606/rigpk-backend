"""
Router tests against a seeded tmp DB.

The repo had no TestClient at all, so every endpoint shipped untested — which
is how /api/parts could return HTTP 200 with an empty body on a misconfigured
deploy without anyone noticing.

There is no FastAPI dependency for the DB yet (routers call `db.database.get_db`
directly inside each handler, opening and closing one `Database`/sqlite3
connection per request) — that lands in a later task. Until then this fixture
monkeypatches the `get_db` name bound into each router module, which is the
actual construction point today, so it points at the same tmp SQLite file the
`seeded_db` fixture wrote to. Each request still opens its own connection to
that file, same as production — sqlite3 connections aren't thread-safe, and
FastAPI dispatches sync route handlers to a threadpool, so reusing a single
open connection across requests fails with "SQLite objects created in a
thread can only be used in that same thread."

Task 2's `get_database()` dependency needs to preserve this: whatever it
constructs must be swappable via `app.dependency_overrides`, and if it hands
out a single long-lived connection (rather than one per request) that
connection must tolerate being used from FastAPI's threadpool.
"""
import pytest
from fastapi.testclient import TestClient

from db.database import Database
import backend.routers.parts as parts_router
import backend.routers.builds as builds_router
import backend.routers.prebuilts as prebuilts_router
import backend.routers.trends as trends_router

_DB_MODULES = (parts_router, builds_router, prebuilts_router, trends_router)


@pytest.fixture
def seeded_db(tmp_path):
    db = Database(tmp_path / "api.db")
    db.upsert_products([
        {"name": f"Asus GeForce RTX 4060 {i}", "price_pkr": 80000 + i * 1000,
         "url": f"https://czone.com.pk/product/rtx-4060-{i}", "category": "gpu",
         "source": "czone", "scraped_at": "2026-08-14T00:00:00Z"}
        for i in range(30)
    ] + [
        {"name": f"AMD Ryzen 5 7600X {i}", "price_pkr": 60000 + i * 500,
         "url": f"https://czone.com.pk/product/r5-7600x-{i}", "category": "cpu",
         "source": "pakbyte", "scraped_at": "2026-08-14T00:00:00Z"}
        for i in range(10)
    ])
    yield db
    db.close()


@pytest.fixture
def client(seeded_db, tmp_path, monkeypatch):
    import backend.main as main

    def _get_db(path=None):
        return Database(tmp_path / "api.db")

    for mod in _DB_MODULES:
        monkeypatch.setattr(mod, "get_db", _get_db)

    with TestClient(main.app) as c:
        yield c
