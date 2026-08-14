"""
/api/parts and /api/trends/groups were shipping fields nothing reads:
`specs` on the list routes (only /build's picker uses it — 12.5% of the
payload), and `method`/`used_count` on every trend point. `specs` is now
opt-in via `include_specs=true`; `last_seen_at` is new so a stale price is
distinguishable from a fresh one (M7).
"""


def test_list_route_omits_specs_by_default(client):
    item = client.get("/api/parts?category=gpu&limit=1").json()["items"][0]
    assert "specs" not in item or item["specs"] is None


def test_picker_can_request_specs(client):
    item = client.get("/api/parts?category=gpu&limit=1&include_specs=true").json()["items"][0]
    assert item["specs"] is not None


def test_parts_expose_freshness(client):
    item = client.get("/api/parts?category=gpu&limit=1").json()["items"][0]
    assert item["last_seen_at"]


def test_trend_points_drop_dead_fields(client):
    body = client.get("/api/trends/groups?category=gpu").json()
    blob = str(body)
    assert "used_count" not in blob and '"method"' not in blob
