"""
The catalogue changes once a week. Serving it with no cache headers means every
visitor re-fetches everything, and Cloudflare (which fronts Render) cannot help.
"""
import pytest


@pytest.mark.parametrize("path,expected_max_age", [
    ("/api/parts?category=gpu&limit=5", 900),
    ("/api/parts/filters?category=gpu", 3600),
    ("/api/trends/groups?category=gpu", 3600),
    ("/api/prebuilts", 900),
    ("/api/stats", 300),
])
def test_read_endpoints_are_cacheable(client, path, expected_max_age):
    r = client.get(path)
    cc = r.headers.get("Cache-Control", "")
    assert f"max-age={expected_max_age}" in cc, f"{path} -> {cc!r}"


def test_search_index_keeps_its_own_header(client):
    cc = client.get("/api/search-index?category=gpu").headers["Cache-Control"]
    assert "max-age=3600" in cc and "stale-while-revalidate" in cc


def test_writes_are_never_cached(client):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    r = client.post("/api/builds/share", json={"gpu": ids[0]})
    assert "no-store" in r.headers.get("Cache-Control", "no-store")
