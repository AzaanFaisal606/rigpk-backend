"""
A new Database per request costs a full TLS handshake to Turso — measured at
552.9 ms, about 55% of market-page backend latency.
"""
import threading


def test_requests_share_one_database(client, monkeypatch):
    """
    ThreadSafeDatabase connects lazily (on the owner thread, on first actual
    call) rather than in __init__ -- required so get_database()'s lock never
    spans a network call (see backend/deps.py). So the one-time connect
    happens on this test's first request, not during fixture setup; warm the
    connection up before monkeypatching, then assert the remaining 5
    requests open none.
    """
    client.get("/api/parts?category=gpu&limit=5")  # forces the lazy connect

    import db.database as dbmod
    constructed = []
    original = dbmod.Database.__init__

    def counting_init(self, *a, **kw):
        constructed.append(1)
        return original(self, *a, **kw)

    monkeypatch.setattr(dbmod.Database, "__init__", counting_init)
    for _ in range(5):
        client.get("/api/parts?category=gpu&limit=5")
    assert not constructed, f"{len(constructed)} connections opened across 5 requests"


def test_concurrent_requests_do_not_crash_or_corrupt(client):
    """
    Fires real concurrent requests at the app through several Python threads.

    This is the property the whole design exists for: FastAPI dispatches sync
    route handlers (and, separately, sync `Depends()` callables) to its own
    threadpool, so a naive shared connection — or even a `threading.local`
    cache keyed by "whichever thread is running right now" — can end up using
    a sqlite3 connection from a different thread than the one that created
    it, which sqlite3 refuses outright. `get_database()`'s `ThreadSafeDatabase`
    wrapper marshals every call onto one dedicated owner thread specifically
    to survive this. If that guarantee ever regressed, this test would fail
    with "SQLite objects created in a thread can only be used in that same
    thread" instead of just being slow.
    """
    errors = []
    results = []
    lock = threading.Lock()

    def worker():
        try:
            r = client.get("/api/parts?category=gpu&limit=5")
            with lock:
                results.append((r.status_code, r.json()))
        except Exception as exc:  # noqa: BLE001 - any failure, from any thread, fails the test
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent requests raised: {errors!r}"
    assert len(results) == 20
    for status_code, body in results:
        assert status_code == 200
        assert len(body["items"]) == 5
        assert body["total"] == 30
