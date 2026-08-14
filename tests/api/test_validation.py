"""
A filter value the server does not understand must be rejected, not ignored.
Silently dropping it returns 200 with a result set that answers a different
question than the one asked.
"""
from db.database import Database


def test_unknown_category_is_400(client):
    r = client.get("/api/parts?category=banana")
    assert r.status_code == 400
    assert "detail" in r.json()


def test_unknown_source_is_400(client):
    assert client.get("/api/parts?source=not-a-retailer").status_code == 400


def test_unknown_sort_is_422(client):
    """sort is pattern-validated by Query(), so FastAPI answers 422 — fine, it is not 200."""
    assert client.get("/api/parts?sort=cheapest").status_code == 422


def test_filters_unknown_category_is_400_not_empty_dict(client):
    r = client.get("/api/parts/filters?category=banana")
    assert r.status_code == 400, "an empty dict reads as 'this category has no filters'"


def test_limit_is_capped(client):
    assert client.get("/api/parts?limit=100000").status_code == 422


def test_ids_are_capped(client):
    ids = ",".join(str(i) for i in range(1, 200))
    assert client.get(f"/api/parts?ids={ids}").status_code == 422


def test_stats_does_not_leak_exception_text(client, seeded_db):
    # seeded_db is a path fixture (see conftest.py) — write the scrape_runs
    # row through a short-lived direct connection, same pattern as
    # test_stats_shape.py, rather than treating the fixture value itself as
    # a Database.
    db = Database(seeded_db)
    db.record_scrape_run(
        "czone", started_at="2026-08-14T00:00:00Z", finished_at="2026-08-14T00:05:00Z",
        ok=False, error="Traceback (most recent call last): File /home/azaan/secret/path.py",
    )
    db.close()

    body = client.get("/api/stats").json()
    blob = str(body)
    assert "Traceback" not in blob
    assert "/home/azaan" not in blob
