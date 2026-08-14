"""
A filter value the server does not understand must be rejected, not ignored.
Silently dropping it returns 200 with a result set that answers a different
question than the one asked.
"""
from db.database import Database

# NOTE (F4, redaction fix round): test_unknown_category_is_400, test_unknown_sort_is_422
# and test_limit_is_capped used to live here but exercised /api/parts behaviour that
# predates this task (category/sort/limit validation were unchanged by the commit that
# added this file — see git show 83e3ef7). They passed against pre-fix code too, so they
# didn't cover anything this task introduced. Moved to
# tests/api/test_parts_preexisting_validation.py and labelled as regression guards.
# The tests below all exercise behaviour this task actually changed: unknown `source`
# now 400 (was silently dropped), `/api/parts/filters` unknown category now 400 (was
# `{}`), `ids` over 50 now 422 (was 400), and /api/stats error redaction.


def test_unknown_source_is_400(client):
    assert client.get("/api/parts?source=not-a-retailer").status_code == 400


def test_filters_unknown_category_is_400_not_empty_dict(client):
    r = client.get("/api/parts/filters?category=banana")
    assert r.status_code == 400, "an empty dict reads as 'this category has no filters'"


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
