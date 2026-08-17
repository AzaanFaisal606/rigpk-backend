"""One assertion per endpoint: it responds, with the right shape."""


def test_parts_returns_items_and_total(client):
    r = client.get("/api/parts?category=gpu&limit=5")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 5
    assert body["total"] == 30


def test_filters_endpoint(client):
    r = client.get("/api/parts/filters?category=gpu")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_stats_endpoint(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert "sources" in r.json()


def test_search_index_sets_etag(client):
    r = client.get("/api/search-index?category=gpu")
    assert r.status_code == 200
    assert r.headers.get("ETag")


def test_share_roundtrip(client):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=2").json()["items"]]
    r = client.post("/api/builds/share", json={"gpu": ids[0], "cpu": ids[1]})
    assert r.status_code == 200
    code = r.json()["code"]
    assert len(code) == 6
    got = client.get(f"/api/builds/share/{code}")
    assert got.status_code == 200
    assert set(got.json()) == {"gpu", "cpu"}


def test_unknown_share_code_is_404(client):
    assert client.get("/api/builds/share/zzzzzz").status_code == 404


def test_prebuilts_endpoint(client):
    r = client.get("/api/prebuilts")
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "total": 0}


def test_trends_groups_endpoint(client):
    r = client.get("/api/trends/groups?category=gpu")
    assert r.status_code == 200
    body = r.json()
    assert body == {"category": "gpu", "groups": []}
