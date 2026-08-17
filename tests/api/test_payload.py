"""
/api/parts and /api/trends/groups were shipping fields nothing reads:
`method`/`used_count` on every trend point (dead everywhere — dropped).
`specs` is read today by the currently-deployed frontend (PartPickerModal,
checkCompatibility()), so it defaults to included — `include_specs=false`
is an opt-out for callers (the market grid, in Phase 4) that don't need it.
`last_seen_at` is new so a stale price is distinguishable from a fresh one
(M7).
"""


def test_list_route_includes_specs_by_default(client):
    # Pinned: the deployed frontend reads `.specs` unconditionally and has
    # no `include_specs` param, so the default must stay populated or the
    # /build picker and compatibility checker silently go blank in prod.
    item = client.get("/api/parts?category=gpu&limit=1").json()["items"][0]
    assert item["specs"] is not None
    assert item["specs"] != {}


def test_grid_can_opt_out_of_specs(client):
    item = client.get("/api/parts?category=gpu&limit=1&include_specs=false").json()["items"][0]
    assert "specs" not in item or item["specs"] is None


def test_parts_expose_freshness(client):
    item = client.get("/api/parts?category=gpu&limit=1").json()["items"][0]
    assert item["last_seen_at"]


def test_trend_points_drop_dead_fields(client):
    body = client.get("/api/trends/groups?category=gpu").json()
    blob = str(body)
    assert "used_count" not in blob and '"method"' not in blob
