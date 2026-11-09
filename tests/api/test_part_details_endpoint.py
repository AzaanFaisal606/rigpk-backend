import json
from pathlib import Path

from db.database import Database

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "details" / "redtech_psu.json"


def _part_id(db_path, url):
    db = Database(db_path, allow_remote_migrations=False)
    try:
        return db._conn.execute("SELECT id FROM parts WHERE url = ?", (url,)).fetchone()[0]
    finally:
        db.close()


def _add_details(db_path):
    from scrapers.details.parse import woo_raw
    item = json.loads(FIX.read_text(encoding="utf-8"))
    db = Database(db_path, allow_remote_migrations=False)
    try:
        db.upsert_products([{
            "name": "Detail PSU", "price_pkr": 20000, "url": "https://redtech.pk/p/psu",
            "category": "psu", "source": "redtech", "scraped_at": "2026-08-14T00:00:00Z",
            "details_raw": woo_raw(item, "redtech"),
        }])
        db._conn.commit()
    finally:
        db.close()


def test_details_returns_parsed_layers(client, seeded_db):
    _add_details(seeded_db)
    pid = _part_id(seeded_db, "https://redtech.pk/p/psu")
    r = client.get(f"/api/parts/{pid}/details")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"overview", "highlights", "specs"}
    assert body["specs"], "redtech PSU fixture has a spec table"
    assert all(len(row) == 3 for row in body["specs"])
    assert "raw" not in body
    assert "max-age" in r.headers["cache-control"]


def test_details_missing_row_is_empty_not_404(client, seeded_db):
    pid = _part_id(seeded_db, "https://czone.com.pk/product/rtx-4060-0")
    r = client.get(f"/api/parts/{pid}/details")
    assert r.status_code == 200
    assert r.json() == {"overview": None, "highlights": [], "specs": []}


def test_details_unknown_id_is_empty(client):
    r = client.get("/api/parts/999999999/details")
    assert r.status_code == 200
    assert r.json()["specs"] == []


def test_details_rejects_bad_id(client):
    assert client.get("/api/parts/0/details").status_code == 400
    assert client.get("/api/parts/abc/details").status_code == 422
