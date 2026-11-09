import json
import sys

import pytest

import run_all
from db.database import get_db
from scripts import backfill_details
from scrapers.details import PARSER_VERSION


def _row(i):
    return {
        "name": f"NVIDIA GeForce RTX 4070 12GB #{i}", "price_pkr": 100_000,
        "url": f"https://zahcomputers.pk/product/rtx-4070-{i}", "category": "gpu",
        "source": "zahcomputers.pk", "scraped_at": "2026-09-18T00:10:00+00:00",
        "thumbnail_url": None,
    }


@pytest.fixture
def harness(tmp_path, monkeypatch):
    db_path = tmp_path / "ppc.db"
    calls = []

    def make(ok=True, raises=False):
        def fake_zah():
            return [_row(i) for i in range(4)]

        def fake_run_source(key, **_k):
            return {"source": "zahcomputers.pk", "started": "x", "products": 4,
                    "ok": ok, "error": None if ok else "boom", "results": fake_zah()}

        def fake_fetch(db, limit, **_k):
            calls.append(limit)
            if raises:
                raise RuntimeError("zah down")
            return {"fetched": 0, "written": 0, "skipped": 0, "stopped": False}

        monkeypatch.setattr(run_all, "DB_PATH", str(db_path))
        monkeypatch.setattr(run_all, "SCRAPERS", {"zah": ("zahcomputers.pk", fake_zah)})
        monkeypatch.setattr(run_all, "run_sources", lambda keys, **_k: [fake_run_source(k) for k in keys])
        monkeypatch.setattr(run_all, "backup_db", lambda *_a, **_k: None)
        monkeypatch.setattr(run_all, "fetch_zah_details", fake_fetch)
        monkeypatch.setattr(sys, "argv", ["run_all.py", "zah"])
        return calls

    return make


def test_zah_ok_runs_details_pass(harness):
    calls = harness(ok=True)
    run_all.main()
    assert calls == [300]


def test_zah_not_ok_skips_details_pass(harness):
    calls = harness(ok=False)
    run_all.main()
    assert calls == []


def test_details_failure_does_not_break_run(harness):
    calls = harness(ok=True, raises=True)
    run_all.main()
    assert calls == [300]


def test_backfill_reparse_rewrites_old_version(tmp_path, monkeypatch):
    path = tmp_path / "b.db"
    raw = {"kind": "woo", "source": "redtech.pk", "short_description": "<p>Hello world</p>",
           "description": "", "attributes": []}
    with get_db(str(path), allow_remote_migrations=True) as db:
        db.upsert_products([_row(1)])
        pid = db._conn.execute("SELECT id FROM parts").fetchone()[0]
        db._conn.execute(
            "INSERT INTO part_details (part_id, overview, highlights, specs, raw, raw_hash, "
            "parser_version, updated_at) VALUES (?, '', '[]', '[]', ?, 'old', 0, 'old')",
            (pid, json.dumps(raw)),
        )
        db._conn.commit()
    monkeypatch.setattr(backfill_details, "DB_PATH", str(path))
    assert backfill_details.main(["--reparse"]) == 0
    with get_db(str(path), allow_remote_migrations=True) as db:
        assert db.get_part_details(pid)["parser_version"] == PARSER_VERSION


def test_backfill_zah_passes_limit(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(backfill_details, "DB_PATH", str(tmp_path / "z.db"))
    monkeypatch.setattr(backfill_details, "fetch_zah_details",
                        lambda db, limit, **_k: seen.append(limit) or {"fetched": 0})
    assert backfill_details.main(["--zah", "--limit", "5"]) == 0
    assert backfill_details.main(["--zah"]) == 0
    assert seen == [5, None]
