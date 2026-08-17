"""
POST /api/builds/share is a public write into a metered database. Anyone can
fill it with rows referencing parts that never existed.

The rate limiter keys on the caller's IP so one visitor can't drain the
shared budget for everyone. Behind Render (the deploy target) that means the
*real* client IP from `X-Forwarded-For`, not the proxy's — see
`backend/routers/builds.py::_client_key`.
"""

import re

import pytest


def test_nonexistent_part_id_is_rejected(client):
    r = client.post("/api/builds/share", json={"gpu": 999999})
    assert r.status_code == 400
    assert "999999" in r.json()["detail"]


def test_valid_build_still_works(client):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    r = client.post("/api/builds/share", json={"gpu": ids[0]})
    assert r.status_code == 200
    assert re.fullmatch(r"[A-Za-z0-9]{6}", r.json()["code"])


def test_rate_limit_kicks_in(client):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    codes = [client.post("/api/builds/share", json={"gpu": ids[0]}).status_code
             for _ in range(40)]
    assert 429 in codes, "no rate limit applied"


def test_oversized_body_is_rejected(client):
    r = client.post("/api/builds/share",
                    json={"gpu": 1, "junk": "x" * 100000})
    assert r.status_code in (400, 413, 422)


def test_different_forwarded_for_do_not_share_a_bucket(client):
    from backend.routers.builds import _RATE

    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    client.post("/api/builds/share", json={"gpu": ids[0]},
                headers={"X-Forwarded-For": "203.0.113.5"})
    client.post("/api/builds/share", json={"gpu": ids[0]},
                headers={"X-Forwarded-For": "198.51.100.9"})

    assert _RATE["203.0.113.5"] and len(_RATE["203.0.113.5"]) == 1
    assert _RATE["198.51.100.9"] and len(_RATE["198.51.100.9"]) == 1


def test_same_forwarded_for_shares_a_bucket(client):
    from backend.routers.builds import _RATE

    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    for _ in range(3):
        client.post("/api/builds/share", json={"gpu": ids[0]},
                    headers={"X-Forwarded-For": "192.0.2.77"})

    assert len(_RATE["192.0.2.77"]) == 3


def test_distrust_proxy_headers_falls_back_to_client_host(client, monkeypatch):
    import backend.routers.builds as builds_module

    monkeypatch.setattr(builds_module, "_TRUST_PROXY_HEADERS", False)
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    client.post("/api/builds/share", json={"gpu": ids[0]},
                headers={"X-Forwarded-For": "203.0.113.5"})

    assert "203.0.113.5" not in builds_module._RATE
    assert "testclient" in builds_module._RATE  # TestClient's request.client.host


def test_429_carries_positive_retry_after(client):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    responses = [
        client.post("/api/builds/share", json={"gpu": ids[0]},
                    headers={"X-Forwarded-For": "172.16.5.9"})
        for _ in range(25)
    ]
    blocked = [r for r in responses if r.status_code == 429]
    assert blocked, "no rate limit applied"
    retry_after = blocked[0].headers.get("retry-after")
    assert retry_after is not None
    assert int(retry_after) > 0


def test_bare_int_slot_still_works_and_defaults_qty_to_one(client):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    code = client.post("/api/builds/share", json={"gpu": ids[0]}).json()["code"]

    resolved = client.get(f"/api/builds/share/{code}").json()
    assert resolved["gpu"]["qty"] == 1


def test_qty_object_round_trips(client):
    item = client.get("/api/parts?category=gpu&limit=1").json()["items"][0]
    code = client.post(
        "/api/builds/share", json={"gpu": {"id": item["id"], "qty": 2}}
    ).json()["code"]

    resolved = client.get(f"/api/builds/share/{code}").json()
    assert resolved["gpu"]["qty"] == 2
    assert resolved["gpu"]["price_at_share"] == item["price_pkr"]


@pytest.mark.parametrize("qty", [0, 5, -1])
def test_qty_out_of_range_is_rejected(client, qty):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    r = client.post("/api/builds/share", json={"gpu": {"id": ids[0], "qty": qty}})
    assert r.status_code == 400


def test_qty_non_int_is_rejected(client):
    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    r = client.post("/api/builds/share", json={"gpu": {"id": ids[0], "qty": "two"}})
    assert r.status_code == 400


def test_price_at_share_is_taken_from_server_not_client(client):
    """Whatever price_at_share the client sends is ignored -- the server
    always snapshots its own current latest_price at share time."""
    item = client.get("/api/parts?category=gpu&limit=1").json()["items"][0]
    code = client.post(
        "/api/builds/share",
        json={"gpu": {"id": item["id"], "qty": 1, "price_at_share": 1}},
    ).json()["code"]

    resolved = client.get(f"/api/builds/share/{code}").json()
    assert resolved["gpu"]["price_at_share"] == item["price_pkr"]
    assert resolved["gpu"]["price_at_share"] != 1


def test_garbage_forwarded_for_does_not_create_a_bucket_key(client):
    from backend.routers.builds import _RATE

    ids = [i["id"] for i in client.get("/api/parts?category=gpu&limit=1").json()["items"]]
    garbage = "not-an-ip-" + ("x" * 5000)
    client.post("/api/builds/share", json={"gpu": ids[0]},
                headers={"X-Forwarded-For": garbage})

    assert garbage not in _RATE
    assert all(len(key) <= 45 for key in _RATE)
