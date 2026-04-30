"""
Retroactive spec enrichment — populates specs for all existing parts rows.
Run from project root: python -m db.enrich_specs
Safe to re-run (idempotent).
"""
from __future__ import annotations
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scrapers.spec_extractor import extract_specs

DB_PATH = Path(__file__).parent.parent / "data" / "ppc.db"


def run():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, name, category FROM parts ORDER BY category, id"
    ).fetchall()

    print(f"Processing {len(rows)} parts...")

    updates: list[tuple[str | None, int]] = []
    stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "with_specs": 0, "with_brand": 0})

    for row in rows:
        cat = row["category"]
        specs = extract_specs(row["name"], cat)
        specs_json = json.dumps(specs, ensure_ascii=False) if specs else None
        updates.append((specs_json, row["id"]))
        stats[cat]["total"] += 1
        if specs:
            stats[cat]["with_specs"] += 1
        if specs.get("brand"):
            stats[cat]["with_brand"] += 1

    conn.executemany("UPDATE parts SET specs = ? WHERE id = ?", updates)
    conn.commit()
    conn.close()

    print(f"\nEnrichment complete. Coverage report:")
    print(f"{'Category':15s} {'Total':>7} {'With Specs':>12} {'With Brand':>12}")
    print("-" * 50)
    for cat in sorted(stats):
        s = stats[cat]
        total = s["total"]
        print(
            f"{cat:15s} {total:>7} "
            f"{s['with_specs']:>7} ({s['with_specs']/total*100:3.0f}%)"
            f"{s['with_brand']:>7} ({s['with_brand']/total*100:3.0f}%)"
        )


if __name__ == "__main__":
    run()
