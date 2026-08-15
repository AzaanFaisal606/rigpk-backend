"""
G2: resolve_shared_build() used to derive price from a price_log correlated
join instead of parts.latest_price -- a different source than list_parts()
and search_index() use, so a shared build could render a stale/mismatched
price, or resolve a part list_parts() would treat as priceless. Now it reads
latest_price, same as everywhere else.

Also covers the qty + price_at_share snapshot shape:
{"id": <int>, "qty": <int>, "price_at_share": <int|None>} per slot, and the
two things that shape had to preserve while changing:
  - a code created before this change (bare int per slot) must still
    resolve, treated as qty=1 with no snapshot
  - a part that's since been delisted, or lost its price, must be returned
    (flagged), not silently dropped from the resolved build
"""
import json

import pytest

from db.database import get_db


@pytest.fixture
def db(tmp_path):
    d = get_db(tmp_path / "t.db")
    d.upsert_products([
        {"name": "MSI RTX 5090 Gaming", "price_pkr": 900000, "url": "https://x.pk/a",
         "category": "gpu", "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z"},
        {"name": "AMD Ryzen 7 9800X3D", "price_pkr": 150000, "url": "https://x.pk/c",
         "category": "cpu", "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z"},
    ])
    yield d
    d.close()


def _part_id(db, name_like):
    return db._conn.execute(
        "SELECT id FROM parts WHERE name LIKE ?", (f"%{name_like}%",)
    ).fetchone()[0]


def test_resolve_reads_latest_price_not_stale_price_log(db):
    gpu_id = _part_id(db, "5090")
    cpu_id = _part_id(db, "9800X3D")
    code = db.create_shared_build({
        "gpu": {"id": gpu_id, "qty": 1, "price_at_share": 900000},
        "cpu": {"id": cpu_id, "qty": 1, "price_at_share": 150000},
    })

    # A newer price_log row must not change what resolve_shared_build
    # reports for price_pkr -- only latest_price does, matching
    # list_parts/search_index.
    db._conn.execute(
        "INSERT INTO price_log (part_id, price_pkr, scraped_at) VALUES (?, ?, ?)",
        (gpu_id, 111111, "2026-08-09T00:00:00Z"),
    )
    db._conn.commit()

    resolved = db.resolve_shared_build(code)
    assert resolved["gpu"]["price_pkr"] == 900000


def test_resolve_returns_slot_with_no_latest_price_instead_of_dropping(db):
    """A part that lost its price (out of stock) is returned, flagged --
    not silently skipped."""
    gpu_id = _part_id(db, "5090")
    cpu_id = _part_id(db, "9800X3D")
    code = db.create_shared_build({
        "gpu": {"id": gpu_id, "qty": 1, "price_at_share": 900000},
        "cpu": {"id": cpu_id, "qty": 1, "price_at_share": 150000},
    })

    db._conn.execute("UPDATE parts SET latest_price = NULL WHERE id = ?", (gpu_id,))
    db._conn.commit()

    resolved = db.resolve_shared_build(code)
    assert "gpu" in resolved
    assert resolved["gpu"]["price_pkr"] is None
    assert resolved["gpu"]["is_active"] is True
    assert resolved["gpu"]["price_at_share"] == 900000
    assert resolved["cpu"]["price_pkr"] == 150000


def test_resolve_returns_delisted_part_instead_of_dropping(db):
    """A part swept inactive is returned, flagged is_active=False with a
    delisted_at timestamp -- not silently skipped."""
    gpu_id = _part_id(db, "5090")
    code = db.create_shared_build({"gpu": {"id": gpu_id, "qty": 1, "price_at_share": 900000}})

    db._conn.execute(
        "UPDATE parts SET is_active = 0, delisted_at = '2026-08-10T00:00:00Z' WHERE id = ?",
        (gpu_id,),
    )
    db._conn.commit()

    resolved = db.resolve_shared_build(code)
    assert "gpu" in resolved
    assert resolved["gpu"]["is_active"] is False
    assert resolved["gpu"]["delisted_at"] == "2026-08-10T00:00:00Z"


def test_resolve_unknown_code_returns_none(db):
    assert db.resolve_shared_build("ZZZZZZ") is None


def test_qty_and_price_snapshot_round_trip(db):
    gpu_id = _part_id(db, "5090")
    code = db.create_shared_build({"gpu": {"id": gpu_id, "qty": 3, "price_at_share": 900000}})

    resolved = db.resolve_shared_build(code)
    assert resolved["gpu"]["qty"] == 3
    assert resolved["gpu"]["price_at_share"] == 900000


def test_old_bare_int_row_still_resolves_with_qty_one(db):
    """Codes created before qty/price-snapshot support stored a bare
    part_id per slot ({"gpu": 42}). Hand-write that shape directly -- as if
    it were a row that predates this change -- and confirm it still
    resolves."""
    gpu_id = _part_id(db, "5090")
    cpu_id = _part_id(db, "9800X3D")
    old_shape = json.dumps({"gpu": gpu_id, "cpu": cpu_id})
    db._conn.execute(
        "INSERT INTO shared_builds (code, build_json) VALUES (?, ?)",
        ("OLDFMT", old_shape),
    )
    db._conn.commit()

    resolved = db.resolve_shared_build("OLDFMT")
    assert resolved["gpu"]["id"] == gpu_id
    assert resolved["gpu"]["qty"] == 1
    assert resolved["gpu"]["price_at_share"] is None
    assert resolved["gpu"]["price_pkr"] == 900000
    assert resolved["cpu"]["qty"] == 1
