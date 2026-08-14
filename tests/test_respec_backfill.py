"""
scripts/migrations/2026_08_14_respec_backfill re-runs extract_specs over
every part and rewrites parts.specs to match. Must be idempotent (a second
run changes nothing) and must leave already-current rows untouched.

No test here touches a remote database — everything runs against a
tmp_path SQLite file via db.database.Database (see conftest._force_local_sqlite).
"""
import importlib.util
import json
from pathlib import Path

import pytest

from db.database import Database

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "migrations" / "2026_08_14_respec_backfill.py"
)
_spec = importlib.util.spec_from_file_location("respec_backfill", _MODULE_PATH)
respec_backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(respec_backfill)

compute_backfill = respec_backfill.compute_backfill
apply_updates = respec_backfill.apply_updates


def _product(name, category, price, url):
    return {
        "name": name, "price_pkr": price, "url": url,
        "category": category, "source": "czone", "scraped_at": "2026-08-01T00:00:00Z",
    }


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _set_stale_specs(db, part_id, specs: dict | None) -> None:
    db._conn.execute(
        "UPDATE parts SET specs = ? WHERE id = ?",
        (json.dumps(specs) if specs else None, part_id),
    )
    db._conn.commit()


def _rows(db):
    return db._conn.execute("SELECT id, name, category, specs FROM parts").fetchall()


def test_stale_gpu_brand_and_missing_cpu_socket_get_rewritten(db):
    db.upsert_products([
        _product("ASUS ROG STRIX RTX 4070 12GB GDDR6X", "gpu", 250000,
                  "https://example.com/p/rtx-4070"),
        _product("AMD Ryzen 5 7500F Desktop Processor", "cpu", 45000,
                  "https://example.com/p/7500f"),
    ])
    gpu_id, cpu_id = (r["id"] for r in db._conn.execute(
        "SELECT id, category FROM parts ORDER BY id"
    ).fetchall())

    # Simulate the pre-fix extractor's output: chip-vendor brand on the GPU
    # (old semantics), no socket at all on the CPU (F-suffix wasn't resolved).
    _set_stale_specs(db, gpu_id, {"brand": "NVIDIA", "vram": "12GB", "model": "RTX 4070"})
    _set_stale_specs(db, cpu_id, {"brand": "AMD", "model": "Ryzen 5 7500F"})

    updates, report = compute_backfill(
        (r["id"], r["name"], r["category"], r["specs"]) for r in _rows(db)
    )
    assert report["rows_examined"] == 2
    assert report["rows_updated"] == 2
    assert {row_id for row_id, _ in updates} == {gpu_id, cpu_id}

    assert report["by_category"]["gpu"]["brand"] == {"gained": 1, "lost": 1}  # NVIDIA -> ASUS
    assert report["by_category"]["cpu"]["socket"] == {"gained": 1, "lost": 0}  # missing -> AM5

    apply_updates(db, updates)

    gpu_specs = json.loads(
        db._conn.execute("SELECT specs FROM parts WHERE id = ?", (gpu_id,)).fetchone()["specs"]
    )
    cpu_specs = json.loads(
        db._conn.execute("SELECT specs FROM parts WHERE id = ?", (cpu_id,)).fetchone()["specs"]
    )
    assert gpu_specs == {"brand": "ASUS", "vram": "12GB", "model": "RTX 4070"}
    assert cpu_specs == {"brand": "AMD", "socket": "AM5", "model": "Ryzen 5 7500F"}


def test_second_run_is_a_no_op(db):
    db.upsert_products([
        _product("ASUS ROG STRIX RTX 4070 12GB GDDR6X", "gpu", 250000,
                  "https://example.com/p/rtx-4070"),
    ])
    gpu_id = db._conn.execute("SELECT id FROM parts").fetchone()["id"]
    _set_stale_specs(db, gpu_id, {"brand": "NVIDIA", "vram": "12GB", "model": "RTX 4070"})

    updates, report = compute_backfill(
        (r["id"], r["name"], r["category"], r["specs"]) for r in _rows(db)
    )
    assert report["rows_updated"] == 1
    apply_updates(db, updates)

    updates2, report2 = compute_backfill(
        (r["id"], r["name"], r["category"], r["specs"]) for r in _rows(db)
    )
    assert updates2 == []
    assert report2["rows_updated"] == 0
    assert report2["by_category"] == {}


def test_rows_already_correct_are_left_untouched(db):
    # upsert_products runs the current extract_specs at insert time, so this
    # row's specs are already up to date and must not be queued for a write.
    db.upsert_products([
        _product("Sapphire PULSE RX 6600 8GB GDDR6", "gpu", 90000,
                  "https://example.com/p/rx-6600"),
    ])
    row = db._conn.execute("SELECT id, specs FROM parts").fetchone()
    original_specs = row["specs"]

    updates, report = compute_backfill(
        (r["id"], r["name"], r["category"], r["specs"]) for r in _rows(db)
    )
    assert updates == []
    assert report["rows_updated"] == 0
    assert report["rows_examined"] == 1

    unchanged = db._conn.execute(
        "SELECT specs FROM parts WHERE id = ?", (row["id"],)
    ).fetchone()["specs"]
    assert unchanged == original_specs
