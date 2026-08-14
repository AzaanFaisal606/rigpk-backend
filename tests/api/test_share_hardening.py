"""
POST /api/builds/share is a public write into a metered database. Anyone can
fill it with rows referencing parts that never existed.
"""


def test_nonexistent_part_id_is_rejected(client):
    r = client.post("/api/builds/share", json={"gpu": 999999})
    assert r.status_code == 400


def test_valid_build_still_works(client):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    assert client.post("/api/builds/share", json={"gpu": ids[0]}).status_code == 200


def test_rate_limit_kicks_in(client):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    codes = [client.post("/api/builds/share", json={"gpu": ids[0]}).status_code
             for _ in range(40)]
    assert 429 in codes, "no rate limit applied"


def test_oversized_body_is_rejected(client):
    r = client.post("/api/builds/share",
                    json={"gpu": 1, "junk": "x" * 100000})
    assert r.status_code in (400, 413, 422)
