"""GET /api/parts/{id} (part page) and GET /api/parts/sitemap."""
import pytest

from backend.routers.parts import reset_sitemap_cache
from db.database import Database

from tests.api.test_part_details_endpoint import _add_details, _part_id


@pytest.fixture(autouse=True)
def _fresh_sitemap_cache():
    reset_sitemap_cache()
    yield
    reset_sitemap_cache()


def _set(db_path, sql, params=()):
    db = Database(db_path, allow_remote_migrations=False)
    try:
        db._conn.execute(sql, params)
        db._conn.commit()
    finally:
        db.close()


def test_part_page_returns_part_and_details(client, seeded_db):
    _add_details(seeded_db)
    pid = _part_id(seeded_db, "https://redtech.pk/p/psu")
    r = client.get(f"/api/parts/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == pid
    assert body["name"] == "Detail PSU"
    assert body["price_pkr"] == 20000
    assert body["is_active"] is True
    assert body["details"]["specs"], "redtech PSU fixture has a spec table"
    assert "raw" not in body["details"]
    assert "max-age" in r.headers["cache-control"]


def test_part_page_without_details_has_empty_details(client, seeded_db):
    pid = _part_id(seeded_db, "https://czone.com.pk/product/rtx-4060-0")
    body = client.get(f"/api/parts/{pid}").json()
    assert body["details"] == {"overview": None, "highlights": [], "specs": []}
    assert body["category"] == "gpu"


def test_part_page_unknown_is_404(client):
    assert client.get("/api/parts/999999999").status_code == 404


def test_part_page_bad_id(client):
    assert client.get("/api/parts/0").status_code == 400
    assert client.get("/api/parts/abc").status_code == 422


def test_part_page_inactive_flag(client, seeded_db):
    pid = _part_id(seeded_db, "https://czone.com.pk/product/rtx-4060-1")
    _set(seeded_db, "UPDATE parts SET is_active = 0 WHERE id = ?", (pid,))
    r = client.get(f"/api/parts/{pid}")
    assert r.status_code == 200
    assert r.json()["is_active"] is False


def test_sitemap_lists_only_indexable(client, seeded_db):
    _add_details(seeded_db)
    with_details = _part_id(seeded_db, "https://redtech.pk/p/psu")
    no_details = _part_id(seeded_db, "https://czone.com.pk/product/rtx-4060-0")
    r = client.get("/api/parts/sitemap")
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()}
    assert with_details in ids
    assert no_details not in ids
    row = next(x for x in r.json() if x["id"] == with_details)
    assert set(row) == {"id", "name", "last_seen_at"}

    _set(seeded_db, "UPDATE parts SET latest_price = NULL WHERE id = ?", (with_details,))
    reset_sitemap_cache()
    assert with_details not in {x["id"] for x in client.get("/api/parts/sitemap").json()}
    _set(seeded_db, "UPDATE parts SET latest_price = 1, is_active = 0 WHERE id = ?", (with_details,))
    reset_sitemap_cache()
    assert with_details not in {x["id"] for x in client.get("/api/parts/sitemap").json()}


def test_sitemap_skips_empty_details_row(client, seeded_db):
    pid = _part_id(seeded_db, "https://czone.com.pk/product/rtx-4060-2")
    _set(seeded_db,
         "INSERT INTO part_details (part_id, raw, raw_hash, overview, highlights, specs, parser_version) "
         "VALUES (?, '{}', 'h', NULL, '[]', '[]', 1)", (pid,))
    assert pid not in {x["id"] for x in client.get("/api/parts/sitemap").json()}


def test_sitemap_pages_through_everything(seeded_db):
    _add_details(seeded_db)
    db = Database(seeded_db, allow_remote_migrations=False)
    try:
        ids = [r[0] for r in db._conn.execute("SELECT id FROM parts WHERE is_active = 1").fetchall()]
        for pid in ids:
            db._conn.execute(
                "INSERT OR IGNORE INTO part_details (part_id, raw, raw_hash, overview, highlights, specs, parser_version) "
                "VALUES (?, '{}', 'h', 'x', '[]', '[]', 1)", (pid,))
        db._conn.commit()
        rows = db.list_indexable_parts(page=7)
        assert sorted(r["id"] for r in rows) == sorted(ids)
    finally:
        db.close()


def test_sitemap_is_cached_in_process(client, seeded_db):
    """A query string must not bust it: each uncached call is a full read of the indexable parts."""
    _add_details(seeded_db)
    pid = _part_id(seeded_db, "https://redtech.pk/p/psu")
    assert pid in {x["id"] for x in client.get("/api/parts/sitemap").json()}
    _set(seeded_db, "UPDATE parts SET is_active = 0 WHERE id = ?", (pid,))
    assert pid in {x["id"] for x in client.get("/api/parts/sitemap?x=1").json()}
