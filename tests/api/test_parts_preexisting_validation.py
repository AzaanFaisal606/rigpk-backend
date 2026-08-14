"""
Regression guards for /api/parts validation that predates the "reject bad input
with a 400 and stop error responses leaking internals" task (commit 83e3ef7).

`git show 83e3ef7 -- backend/routers/parts.py` shows the category-validation branch,
the `sort` pattern on `Query()`, and the `limit` bound on `Query()` were all untouched
by that commit — only `source` (silently dropped -> 400) and `ids` (400 -> 422) were
new. These three therefore passed against the pre-task code too and covered nothing
that task introduced; they're kept here, clearly labelled, purely so nobody has to
rediscover "does an unknown category 400" by trial and error later.
"""


def test_unknown_category_is_400(client):
    r = client.get("/api/parts?category=banana")
    assert r.status_code == 400
    assert "detail" in r.json()


def test_unknown_sort_is_422(client):
    """sort is pattern-validated by Query(), so FastAPI answers 422 — fine, it is not 200."""
    assert client.get("/api/parts?sort=cheapest").status_code == 422


def test_limit_is_capped(client):
    assert client.get("/api/parts?limit=100000").status_code == 422
