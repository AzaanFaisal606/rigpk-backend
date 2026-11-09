import json
from pathlib import Path

import pytest

import scrapers.details as details
from db.database import Database
from scrapers.details.parse import woo_raw

FIX = Path(__file__).parent / "fixtures" / "details" / "redtech_psu.json"


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db", allow_remote_migrations=False)
    yield d
    d.close()


def _item():
    return json.loads(FIX.read_text(encoding="utf-8"))


def _p(i=1, source="zahcomputers", raw=None, **kw):
    p = {
        "name": f"Test Card {i}", "price_pkr": 100000,
        "url": f"https://example.com/p/card-{i}",
        "category": "gpu", "source": source, "scraped_at": "2026-08-01T00:00:00Z",
    }
    if raw is not None:
        p["details_raw"] = raw
    p.update(kw)
    return p


def _raw(desc=None):
    item = _item()
    if desc is not None:
        item["short_description"] = desc
        item["description"] = ""
    return woo_raw(item, "redtech.pk")


def _pid(db, i=1):
    return db._conn.execute(
        "SELECT id FROM parts WHERE url = ?", (f"https://example.com/p/card-{i}",)
    ).fetchone()[0]


def _written(db, items):
    n = db.write_details(items)
    db._conn.commit()
    return n


def test_upsert_writes_details(db):
    db.upsert_products([_p(raw=_raw())])
    d = db.get_part_details(_pid(db))
    assert d["specs"]
    assert d["parser_version"] == details.PARSER_VERSION


def test_unchanged_raw_not_rewritten(db):
    raw = _raw()
    db.upsert_products([_p(raw=raw)])
    pid = _pid(db)
    db._conn.execute("UPDATE part_details SET updated_at = 'old' WHERE part_id = ?", (pid,))
    db._conn.commit()
    assert _written(db, [(pid, raw, "Test Card 1")]) == 0
    db.upsert_products([_p(raw=raw)])
    assert db.get_part_details(pid)["updated_at"] == "old"


def test_changed_raw_rewritten(db):
    db.upsert_products([_p(raw=_raw())])
    pid = _pid(db)
    new = _raw("<p>" + "A brand new overview sentence about this power supply unit. " * 2 + "</p>")
    assert _written(db, [(pid, new, "Test Card 1")]) == 1
    assert "brand new overview" in db.get_part_details(pid)["overview"]


def test_parser_version_bump_rewrites(db, monkeypatch):
    raw = _raw()
    db.upsert_products([_p(raw=raw)])
    pid = _pid(db)
    bumped = details.PARSER_VERSION + 1
    monkeypatch.setattr(details, "PARSER_VERSION", bumped)
    assert _written(db, [(pid, raw, "Test Card 1")]) == 1
    assert db.get_part_details(pid)["parser_version"] == bumped


def test_product_without_raw_keeps_existing_details(db):
    db.upsert_products([_p(raw=_raw())])
    pid = _pid(db)
    before = db.get_part_details(pid)
    db.upsert_products([_p()])
    assert db.get_part_details(pid) == before


def test_duplicate_staged_product_writes_one_row(db):
    raw = _raw()
    db.upsert_products([_p(raw=raw), _p(raw=raw)])
    n = db._conn.execute("SELECT COUNT(*) FROM part_details").fetchone()[0]
    assert n == 1


def test_parts_missing_details(db):
    db.upsert_products([_p(1, raw=_raw()), _p(2), _p(3, source="czone")])
    missing = db.parts_missing_details("zahcomputers", 10)
    assert [m["id"] for m in missing] == [_pid(db, 2)]
    assert set(missing[0]) == {"id", "url", "name"}
    db._conn.execute("UPDATE parts SET is_active = 0 WHERE id = ?", (_pid(db, 2),))
    db._conn.commit()
    assert db.parts_missing_details("zahcomputers", 10) == []


def test_cascade_on_part_delete(db):
    db.upsert_products([_p(raw=_raw())])
    pid = _pid(db)
    db._conn.execute("DELETE FROM parts WHERE id = ?", (pid,))
    db._conn.commit()
    assert db.get_part_details(pid) is None


def test_iter_reparse_pages(db):
    db.upsert_products([_p(i, raw=_raw()) for i in (1, 2, 3)])
    got = list(db.iter_details_for_reparse(details.PARSER_VERSION + 1, page=2))
    assert [g[0] for g in got] == sorted(_pid(db, i) for i in (1, 2, 3))
    assert got[0][1]["kind"] == "woo"
    assert list(db.iter_details_for_reparse(details.PARSER_VERSION)) == []
