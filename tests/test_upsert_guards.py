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


def test_repeat_rejection_dedupes_instead_of_appending(db):
    """
    The same junk listing shows up again every weekly scrape. It must UPDATE
    the existing quarantine row (bumping times_rejected, moving last_seen_at)
    rather than growing the table forever.
    """
    db.upsert_products([_p("RGB Light Bar Strip", "monitor", 50000)])
    first = db.list_quarantined()[0]

    db.upsert_products([_p("RGB Light Bar Strip", "monitor", 51000)])
    rows = db.list_quarantined()

    assert len(rows) == 1
    assert rows[0]["times_rejected"] == 2
    assert rows[0]["first_seen_at"] == first["first_seen_at"]


@pytest.mark.parametrize("name,category", [
    ("Adata 16GB (1x16GB) 5600MHz C46 DDR5 SO-DIMM Laptop Memory", "ram"),
    ("Ease RAM 16GB 3200MHZ DDR4 Notebook", "ram"),
    ("16GB Laptop 3200mhz DDR4 PULLED", "ssd"),
    ("ADATA Elite SE880 500GB External Solid State Drive Blue", "ssd"),
    ("Corsair EX100U 1TB Portable USB Type-C SSD", "ssd"),
    ("WD My Book 12TB External Desktop Hard Drive", "hdd"),
    ("Synology DiskStation DS225+ 2-Bay NAS, Diskless", "hdd"),
    ("Philips 50PUT7406/98 - 50\" 4K UHD LED Android TV", "monitor"),
    ("Philips 8100 Series 55PQT8169/98 55\" 4K UHD QLED Smart Google TV", "monitor"),
    ("MXG EMA-12 Single Monitor Arm", "monitor"),
    ("Thermalright Trofeo Vision LCD AIO Display 6.86\" PC Monitor", "monitor"),
    ("Corsair MP600 Elite 1TB Gen4 M.2 NVMe SSD for PS5", "cooling"),
    ("Aula WIN60 HE PRO 60% Mechanical Gaming Keyboard", "gpu"),
    ("Ease Mini PC 1135 - Core i5 - 1145G7", "cpu"),
    ("CoolerMaster MasterFrame Case Panel - Stone Black", "case"),
    ("Used PC Case – Mixed lot – without fans", "case"),
])
def test_misfiled_listings_are_rejected(db, name, category):
    db.upsert_products([_p(name, category, 50000)])
    assert db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 0


@pytest.mark.parametrize("name,category", [
    ("ASUS ROG Strix OLED XG34WCDMTG 34\" QD-OLED Smart Gaming Monitor – 240Hz, Google TV", "monitor"),
    ("Samsung 990 PRO 1TB SSD PCIe 4.0 M.2 2280 Internal Solid State Hard Drive", "ssd"),
    ("Transcend 255S 1TB NVMe Gen4 SSD, Storage Expansion for PS5", "ssd"),
    ("Seagate IronWolf 4TB NAS 3.5\" SATA Hard Drive", "hdd"),
    ("Lian Li A3 Wood Front & Side Tempered Glass Panel Casing - Black", "case"),
])
def test_lookalikes_survive(db, name, category):
    db.upsert_products([_p(name, category, 50000)])
    assert db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 1


@pytest.mark.parametrize("name,expected", [
    ("Seagate IronWolf ST8000VN004 8TB NAS 3.5\" SATA Hard Drive", "hdd"),
    ("4TB SATAIII HDD – used – 100% Health", "hdd"),
    ("WESTERN DIGITAL Red Plus 6TB NAS Hard Drive, 5640 RPM", "hdd"),
    ("Seagate Barracuda SATA SSD 240GB 2.5\" Internal SSD", "ssd"),
    ("Samsung 990 PRO 2TB NVMe M.2 Internal Solid State Hard Drive", "ssd"),
])
def test_hdds_filed_as_ssd_move_to_hdd(db, name, expected):
    db.upsert_products([_p(name, "ssd", 50000)])
    assert db._conn.execute("SELECT category FROM parts").fetchone()[0] == expected


def test_same_source_duplicate_names_keep_the_cheapest(db):
    db.upsert_products([
        _p("WD Black SN850X 1TB M.2 NVMe Gen4 SSD", "ssd", 59999, url="https://pakbyte.pk/products/sn850x"),
        _p("WD Black SN850X 1TB M.2 NVMe Gen4 SSD", "ssd", 54799, url="https://pakbyte.pk/products/sn850x-1"),
        _p("WD Black SN850X 2TB M.2 NVMe Gen4 SSD", "ssd", 99999, url="https://pakbyte.pk/products/sn850x-2tb"),
    ])
    rows = db._conn.execute("SELECT url, latest_price FROM parts ORDER BY url").fetchall()
    assert [tuple(r) for r in rows] == [
        ("https://pakbyte.pk/products/sn850x-1", 54799),
        ("https://pakbyte.pk/products/sn850x-2tb", 99999),
    ]
    rules = [r["rule"] for r in db.list_quarantined()]
    assert rules == ["duplicate_of:https://pakbyte.pk/products/sn850x-1"]
