"""Tests for the per-category client search index."""
import pytest

from db.database import get_db


@pytest.fixture
def db(tmp_path):
    d = get_db(tmp_path / "t.db")
    d.upsert_products([
        {"name": "MSI RTX 5090 Gaming", "price_pkr": 900000, "url": "https://x.pk/a",
         "category": "gpu", "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z"},
        {"name": "Asus RTX 5080 TUF", "price_pkr": 500000, "url": "https://x.pk/b",
         "category": "gpu", "source": "pakbyte.pk", "scraped_at": "2026-08-07T00:00:00Z"},
        {"name": "AMD Ryzen 7 9800X3D", "price_pkr": 150000, "url": "https://x.pk/c",
         "category": "cpu", "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z"},
    ])
    yield d
    d.close()


def test_index_is_scoped_to_category(db):
    idx = db.search_index("gpu")
    assert len(idx["rows"]) == 2
    assert all(isinstance(r[0], int) for r in idx["rows"])


def test_index_row_shape(db):
    idx = db.search_index("gpu")
    row = next(r for r in idx["rows"] if "5090" in r[1])
    part_id, name, src_idx, price = row
    assert name == "MSI RTX 5090 Gaming"
    assert idx["srcs"][src_idx] == "czone.com.pk"
    assert price == 900000


def test_index_excludes_inactive(db):
    db._conn.execute("UPDATE parts SET is_active = 0 WHERE name LIKE '%5080%'")
    db._conn.commit()
    assert len(db.search_index("gpu")["rows"]) == 1


def test_index_version_changes_with_content(db):
    before = db.search_index("gpu")["version"]
    db.upsert_products([
        {"name": "Gigabyte RTX 5070", "price_pkr": 300000, "url": "https://x.pk/d",
         "category": "gpu", "source": "czone.com.pk", "scraped_at": "2026-08-08T00:00:00Z"},
    ])
    assert db.search_index("gpu")["version"] != before


def test_index_version_stable_when_unchanged(db):
    assert db.search_index("gpu")["version"] == db.search_index("gpu")["version"]


def test_unknown_category_is_empty(db):
    idx = db.search_index("nonsense")
    assert idx["rows"] == []
