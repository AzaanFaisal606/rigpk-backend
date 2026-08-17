"""
Router tests against a seeded tmp DB.

The repo had no TestClient at all, so every endpoint shipped untested — which
is how /api/parts could return HTTP 200 with an empty body on a misconfigured
deploy without anyone noticing.

Routes now take `db: Database = Depends(get_database)` (see `backend/deps.py`)
instead of building their own `Database` per request, so the fixture overrides
that dependency via `app.dependency_overrides` rather than monkeypatching a
`get_db` name inside each router module — the latter would silently stop
doing anything the moment the routers stopped calling `get_db` themselves.

The override hands out a `ThreadSafeDatabase` (the same wrapper production
uses), not a raw `Database`. That's not optional: FastAPI resolves each sync
`Depends()` callable via its own separate threadpool dispatch, independent of
the one used for the route handler body, so the thread that returns the
database is frequently not the thread that ends up calling methods on it —
confirmed by seeing this exact fixture crash with sqlite3's "created in a
thread can only be used in that same thread" on nothing more than a single
`/api/stats` call when it returned a raw `Database` built on the pytest
thread. `ThreadSafeDatabase` marshals every call onto its own dedicated owner
thread, so it tolerates being invoked from whichever worker thread FastAPI
picks — sequentially or, per test_connection_reuse.py's concurrency test,
from several threads firing requests at once.
"""
import pytest
from fastapi.testclient import TestClient

from db.database import Database
from backend.deps import ThreadSafeDatabase, get_database


@pytest.fixture(autouse=True)
def _reset_share_rate_limit():
    """
    `backend/routers/builds.py` keys its share-rate-limit bucket on client IP,
    and `TestClient` always presents the same IP ("testclient") — so without
    this, one test's requests count against the next test's budget. Real
    traffic doesn't have this problem (distinct IPs); this fixture just gives
    each test its own clean bucket, the same way a fresh dyno would start.
    """
    from backend.routers.builds import _RATE

    _RATE.clear()
    yield
    _RATE.clear()


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
    db.close()
    yield tmp_path / "api.db"


@pytest.fixture
def client(seeded_db):
    import backend.main as main

    safe_db = ThreadSafeDatabase(seeded_db)
    main.app.dependency_overrides[get_database] = lambda: safe_db
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        main.app.dependency_overrides.pop(get_database, None)
        safe_db.close()
