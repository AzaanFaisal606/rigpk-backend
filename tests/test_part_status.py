"""
delisted_at records WHEN a part left the catalogue, not just that it did.

last_seen_at moves on every scrape and cannot answer "has this gone away?".
resolve_part_status is the shared read path: shared builds use it now, a
favourites/watch-list feature will use it unchanged later.
"""
import pytest

from db.database import Database


def _product(url_id, at="2026-08-14T00:00:00Z", price=500000):
    return {
        "name": f"Test GPU {url_id}", "price_pkr": price,
        "url": f"https://example.com/p/gpu-{url_id}", "category": "gpu",
        "source": "czone", "scraped_at": at,
    }


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_active_part_has_no_delisted_at(db):
    db.upsert_products([_product(1)])
    pid = db._conn.execute("SELECT id FROM parts").fetchone()["id"]
    status = db.resolve_part_status([pid])
    assert status[pid]["is_active"] is True
    assert status[pid]["delisted_at"] is None


def test_sweep_stamps_delisted_at(db):
    db.upsert_products([_product(1), _product(2)])
    ids = [r["id"] for r in db._conn.execute("SELECT id FROM parts ORDER BY id")]
    # Second run sees only the first product -> the second is swept.
    db.upsert_products([_product(1, at="2026-08-21T00:00:00Z")])
    db.deactivate_unseen_parts("czone")

    status = db.resolve_part_status(ids)
    assert status[ids[0]]["is_active"] is True
    assert status[ids[0]]["delisted_at"] is None
    assert status[ids[1]]["is_active"] is False
    assert status[ids[1]]["delisted_at"] is not None


def test_relisting_clears_delisted_at(db):
    db.upsert_products([_product(1), _product(2)])
    ids = [r["id"] for r in db._conn.execute("SELECT id FROM parts ORDER BY id")]
    db.upsert_products([_product(1, at="2026-08-21T00:00:00Z")])
    db.deactivate_unseen_parts("czone")
    assert db.resolve_part_status(ids)[ids[1]]["delisted_at"] is not None

    db.upsert_products([_product(1, at="2026-08-28T00:00:00Z"),
                        _product(2, at="2026-08-28T00:00:00Z")])
    status = db.resolve_part_status(ids)
    assert status[ids[1]]["is_active"] is True
    assert status[ids[1]]["delisted_at"] is None


def test_unknown_ids_are_absent_not_error(db):
    assert db.resolve_part_status([999999]) == {}


def test_empty_input_returns_empty(db):
    assert db.resolve_part_status([]) == {}
