"""
The sweep must work when a source has thousands of parts.

It binds one parameter per seen id; pakbyte alone has 2,589 active rows, and
some SQLite builds cap a statement at 999 host parameters. A scratch table
sidesteps the limit entirely and is faster besides.
"""
import pytest

from db.database import Database


def _p(i, at="2026-08-14T00:00:00Z"):
    return {
        "name": f"Test GPU {i}", "price_pkr": 50000 + i,
        "url": f"https://example.com/p/gpu-{i}", "category": "gpu",
        "source": "pakbyte", "scraped_at": at,
    }


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_sweep_handles_thousands_of_seen_ids(db):
    db.upsert_products([_p(i) for i in range(3000)])
    # Next run sees the first 2,500 only.
    db.upsert_products([_p(i, at="2026-08-21T00:00:00Z") for i in range(2500)])
    swept = db.deactivate_unseen_parts("pakbyte")
    assert swept == 500
    active = db._conn.execute(
        "SELECT COUNT(*) FROM parts WHERE is_active = 1"
    ).fetchone()[0]
    assert active == 2500


def test_sweep_is_scoped_to_its_source(db):
    db.upsert_products([_p(1)])
    db.upsert_products([{
        "name": "Other GPU", "price_pkr": 60000,
        "url": "https://example.com/p/other", "category": "gpu",
        "source": "czone", "scraped_at": "2026-08-14T00:00:00Z",
    }])
    db.upsert_products([_p(2, at="2026-08-21T00:00:00Z")])
    db.deactivate_unseen_parts("pakbyte")
    czone_active = db._conn.execute(
        "SELECT is_active FROM parts WHERE source = 'czone'"
    ).fetchone()["is_active"]
    assert czone_active == 1
