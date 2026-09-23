"""
--strict fails only on real breakage.

A warning (a category that shrank 40-70%) keeps the run green, so it doesn't
start a paid self-heal session, and is handed to the notify job via
$GITHUB_OUTPUT instead.
"""
import sys

import pytest

import run_all
from db.database import get_db


def _row(i: int) -> dict:
    return {
        "name": f"NVIDIA GeForce RTX 4070 12GB #{i}", "price_pkr": 100_000,
        "url": f"https://czone.com.pk/product/rtx-4070-{i}", "category": "gpu",
        "source": "czone.com.pk", "scraped_at": "2026-09-18T00:10:00+00:00", "thumbnail_url": None,
    }


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """10 active czone GPUs; the fake scraper decides how many come back."""
    db_path = tmp_path / "ppc.db"
    with get_db(str(db_path)) as db:
        db.upsert_products([_row(i) for i in range(10)])

    def use(n_back: int):
        monkeypatch.setattr(run_all, "SCRAPERS", {"czone": ("czone.com.pk", lambda: [_row(i) for i in range(n_back)])})

    monkeypatch.setattr(run_all, "DB_PATH", str(db_path))
    monkeypatch.setattr(run_all, "backup_db", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", ["run_all.py", "czone", "--strict"])
    return use


def test_warning_only_run_stays_green_and_exports_warnings(seeded, tmp_path, monkeypatch, capsys):
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    seeded(5)                                   # 10 -> 5: -50%, a warning

    run_all.main()                              # no SystemExit under --strict

    assert "WARNINGS (1)" in capsys.readouterr().out
    exported = out_file.read_text()
    assert exported.startswith("warnings<<") and "czone.com.pk/gpu: 10 → 5" in exported


def test_collapse_fails_strict(seeded):
    seeded(2)                                   # 10 -> 2: -80%, a failure
    with pytest.raises(SystemExit) as exc:
        run_all.main()
    assert exc.value.code == 1


def test_no_github_output_outside_actions(seeded, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    seeded(5)
    run_all.main()                              # warnings printed, nothing written, no error
