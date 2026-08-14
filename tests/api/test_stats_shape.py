"""
/api/stats is consumed by the landing page's source cards and STALE ribbon.
This locks its shape so the round-trip collapse cannot quietly change it,
and pins the query count so it can't quietly regress back to six.
"""
from backend.deps import get_database
from db.database import Database

_SOURCE_HEALTH_KEYS = {
    "stale", "last_run_at", "last_success_at", "last_products",
    "before_active", "after_active", "last_swept", "last_error",
}


def test_stats_shape_is_unchanged(client, seeded_db):
    # Seed one scrape_runs row directly so `sources` isn't empty — the
    # fixture's upsert_products path doesn't record one.
    db = Database(seeded_db)
    db.record_scrape_run(
        "czone", kind="parts", started_at="2026-08-14T00:00:00Z",
        finished_at="2026-08-14T00:05:00Z", products=30, ok=True,
        swept=0, before_active=30, after_active=30,
    )
    db.close()

    body = client.get("/api/stats").json()

    assert set(body) == {
        "total_parts", "total_price_rows", "by_source", "by_category", "sources",
    }
    assert body["sources"], "expected at least one seeded source"
    for source, health in body["sources"].items():
        assert _SOURCE_HEALTH_KEYS <= set(health), f"{source} lost a field"


class _CountingConn:
    """Wraps a live connection to count `execute()` calls without touching
    it — sqlite3.Connection.execute is a read-only attribute, so it can't be
    monkeypatched directly (same issue as db/database.py's _NoCommitConnection)."""

    def __init__(self, conn, calls):
        self._wrapped = conn
        self._calls = calls

    def execute(self, *args, **kwargs):
        self._calls.append(args[0] if args else None)
        return self._wrapped.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


def test_stats_issues_few_queries(client):
    client.get("/api/parts?category=gpu&limit=1")  # force the lazy connect

    inst = client.app.dependency_overrides[get_database]()
    real_db = inst._db
    calls: list = []
    real_db._conn = _CountingConn(real_db._conn, calls)

    client.get("/api/stats")

    assert len(calls) <= 2, f"{len(calls)} queries: {[str(c)[:60] for c in calls]}"
