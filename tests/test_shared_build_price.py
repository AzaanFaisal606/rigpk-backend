"""
G2: resolve_shared_build() used to derive price from a price_log correlated
join instead of parts.latest_price — a different source than list_parts()
and search_index() use, so a shared build could render a stale/mismatched
price, or resolve a part list_parts() would treat as priceless. Now it reads
latest_price and skips parts with none, same as everywhere else.
"""
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
    code = db.create_shared_build({"gpu": gpu_id, "cpu": cpu_id})

    # A newer price_log row must not change what resolve_shared_build
    # reports — only latest_price does, matching list_parts/search_index.
    db._conn.execute(
        "INSERT INTO price_log (part_id, price_pkr, scraped_at) VALUES (?, ?, ?)",
        (gpu_id, 111111, "2026-08-09T00:00:00Z"),
    )
    db._conn.commit()

    resolved = db.resolve_shared_build(code)
    assert resolved["gpu"]["price_pkr"] == 900000


def test_resolve_skips_slot_with_no_latest_price(db):
    gpu_id = _part_id(db, "5090")
    cpu_id = _part_id(db, "9800X3D")
    code = db.create_shared_build({"gpu": gpu_id, "cpu": cpu_id})

    db._conn.execute("UPDATE parts SET latest_price = NULL WHERE id = ?", (gpu_id,))
    db._conn.commit()

    resolved = db.resolve_shared_build(code)
    assert "gpu" not in resolved
    assert resolved["cpu"]["price_pkr"] == 150000


def test_resolve_unknown_code_returns_none(db):
    assert db.resolve_shared_build("ZZZZZZ") is None
