"""
One-shot backfill for the price-trends feature.

  1. Re-extract specs for every gpu/cpu part so existing rows gain the new
     `model` key (specs are otherwise only recomputed on a fresh scrape).
  2. Rebuild the price_trends table from all historical price_log rows.

Run from project root:  python -m scripts.migrations.backfill_models_and_trends
Safe to re-run (idempotent — specs overwritten, trends DELETE+rebuild).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

from scrapers.spec_extractor import extract_specs  # noqa: E402
from db.database import get_db  # noqa: E402

DB_PATH = _ROOT / "data" / "ppc.db"


def _backfill_specs() -> Counter:
    """
    Re-extract specs for gpu/cpu/ram parts so existing rows gain the new keys
    (gpu/cpu `model`, ram `capacity`). Skips rows that re-extract to empty so we
    never null out previously-stored specs. Returns model counts.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, category FROM parts WHERE category IN ('gpu', 'cpu', 'ram')"
    ).fetchall()

    updates: list[tuple[str, int]] = []
    models: Counter = Counter()
    for r in rows:
        specs = extract_specs(r["name"], r["category"])
        if not specs:
            continue  # don't overwrite existing specs with NULL
        updates.append((json.dumps(specs, ensure_ascii=False), r["id"]))
        if specs.get("model"):
            models[f"{r['category']}:{specs['model']}"] += 1

    conn.executemany("UPDATE parts SET specs = ? WHERE id = ?", updates)
    conn.commit()
    conn.close()

    gpu_hit = sum(v for k, v in models.items() if k.startswith("gpu:"))
    cpu_hit = sum(v for k, v in models.items() if k.startswith("cpu:"))
    print(f"Specs backfilled: {len(updates)} of {len(rows)} gpu/cpu/ram parts updated.")
    print(f"  models matched — gpu: {gpu_hit}, cpu: {cpu_hit}")
    print(f"  distinct models — {len(models)}")
    return models


def run():
    print(f"Backfilling models + trends in {DB_PATH}\n")
    _backfill_specs()

    print("\nRebuilding price_trends from price_log ...")
    with get_db(DB_PATH) as db:
        n = db.rebuild_price_trends()
    print(f"price_trends rebuilt: {n} rows written.")


if __name__ == "__main__":
    run()
