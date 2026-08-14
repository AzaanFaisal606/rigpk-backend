"""
upsert_products' three guards, and the quarantine record they now write.

Silent dropping is how an over-broad blocklist term eats real products without
anyone noticing. Every rejection is recorded with the rule that caused it.
"""
import pytest

from db.database import Database


def _p(name, category, price, url="https://example.com/p/x"):
    return {
        "name": name, "price_pkr": price, "url": url, "category": category,
        "source": "czone", "scraped_at": "2026-08-14T00:00:00Z",
    }


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


@pytest.mark.parametrize("category,price,kept", [
    ("gpu", 3999, False), ("gpu", 4000, True),
    ("cpu", 7999, False), ("cpu", 8000, True),
    ("hdd", 1499, False), ("hdd", 1500, True),
    ("monitor", 4999, False), ("monitor", 5000, True),
])
def test_min_price_floor(db, category, price, kept):
    db.upsert_products([_p(f"Some {category}", category, price)])
    n = db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
    assert (n == 1) is kept


@pytest.mark.parametrize("name,category", [
    ("Redragon Mechanical Keyboard RGB", "monitor"),
    ("RGB Light Bar Strip", "monitor"),
    ("USB Docking Station", "hdd"),
    ("Portable SSD 1TB External", "hdd"),
])
def test_category_blocklist_rejects_junk(db, name, category):
    db.upsert_products([_p(name, category, 50000)])
    assert db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 0


@pytest.mark.parametrize("name,category", [
    ("Samsung 27 inch Odyssey G5 Gaming Monitor", "monitor"),
    ("Seagate Barracuda 2TB 7200RPM Hard Drive", "hdd"),
])
def test_real_products_survive(db, name, category):
    db.upsert_products([_p(name, category, 50000)])
    assert db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 1


def test_rejected_row_is_quarantined_with_its_rule(db):
    db.upsert_products([_p("RGB Light Bar Strip", "monitor", 50000)])
    rows = db.list_quarantined()
    assert len(rows) == 1
    assert rows[0]["name"] == "RGB Light Bar Strip"
    assert rows[0]["rule"].startswith("blocklist:")


def test_floor_rejection_records_the_floor(db):
    db.upsert_products([_p("Cheap GPU", "gpu", 100)])
    rows = db.list_quarantined()
    assert rows[0]["rule"] == "min_price:gpu:4000"


def test_null_price_is_skipped_but_not_quarantined(db):
    """A hidden price is normal out-of-stock behaviour, not a data-quality fault."""
    db.upsert_products([_p("Some GPU", "gpu", None)])
    assert db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 0
    assert db.list_quarantined() == []
