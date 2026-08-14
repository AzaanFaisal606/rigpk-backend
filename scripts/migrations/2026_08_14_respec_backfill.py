"""
Re-run extract_specs over every part and rewrite parts.specs.

Needed because the extractor changed: brand now means the manufacturer
rather than the chip vendor (wrong on ~57.7% of GPUs), CPU socket is derived
from the model via the desktop-suffix table (10.8% fill before), and
motherboard form_factor is extracted at all (0% before). Without this,
existing rows keep the old values until each is next scraped — and inactive
rows keep them forever.

Full rewrite, not a NULL-only fill: the old `brand` values are wrong, not
just missing, so every row's specs are unconditionally recomputed and
replaced. No key is preserved across the rewrite — if a category no longer
produces a key extract_specs used to produce, that key is dropped. That is
deliberate: a dropped key here means the extractor decided it can no longer
support that value with confidence, and keeping the stale value around would
be worse than losing it.

Idempotent: a row is only queued for a write when its recomputed specs
differ (by dict equality) from its stored specs, so a second run finds
nothing to change and issues zero UPDATEs.

Batched: writes go out as CASE-over-id UPDATEs of up to BATCH rows each (3
bound params per row — id, specs, id again for the IN clause), the same
pattern as 2026_08_07_name_norm_backfill.py, to stay comfortably under
SQLite's default 999-variable limit and keep remote-Turso round trips low.

Usage:
    set -a && . ./.env.refactor && set +a
    python scripts/migrations/2026_08_14_respec_backfill.py [--apply] [--yes-production]
"""
import json
import sys
from collections import defaultdict

from scripts.migrations._guard import resolve_target
from db.database import Database
from scrapers.spec_extractor import extract_specs

# 300 rows = up to 900 bound params (id + specs per CASE arm, + id per IN
# entry) — under SQLite's 999-variable ceiling with headroom.
BATCH = 300


def _load_specs(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def compute_backfill(rows):
    """
    rows: iterable of (id, name, category, specs_json_or_None) tuples.

    Returns (updates, report):
      updates -- list of (id, new_specs_dict) for rows whose recomputed
                 specs differ from what's stored.
      report  -- {"rows_examined": int, "rows_updated": int,
                  "by_category": {category: {key: {"gained": n, "lost": n}}}}
                 gained/lost count *value* changes per key, not mere key
                 presence: a key whose value changed (e.g. gpu.brand
                 NVIDIA -> ASUS) counts as both a loss (old value) and a
                 gain (new value); a key that newly appears counts only as
                 a gain; a key that disappears counts only as a loss.
    """
    updates = []
    by_category = defaultdict(lambda: defaultdict(lambda: {"gained": 0, "lost": 0}))
    examined = 0
    for row_id, name, category, specs_json in rows:
        examined += 1
        old = _load_specs(specs_json)
        new = extract_specs(name, category) or {}
        if new == old:
            continue
        for key in set(old) | set(new):
            old_v, new_v = old.get(key), new.get(key)
            if old_v == new_v:
                continue
            if new_v:
                by_category[category][key]["gained"] += 1
            if old_v:
                by_category[category][key]["lost"] += 1
        updates.append((row_id, new))

    return updates, {
        "rows_examined": examined,
        "rows_updated": len(updates),
        "by_category": {c: dict(keys) for c, keys in by_category.items()},
    }


def apply_updates(db, updates) -> None:
    """Write (id, new_specs_dict) pairs to parts.specs in bounded batches."""
    for i in range(0, len(updates), BATCH):
        chunk = updates[i:i + BATCH]
        arms = " ".join("WHEN ? THEN ?" for _ in chunk)
        holes = ",".join("?" for _ in chunk)
        params: list = []
        for row_id, new in chunk:
            params.extend([row_id, json.dumps(new, ensure_ascii=False) if new else None])
        params.extend([row_id for row_id, _ in chunk])
        db._conn.execute(
            f"UPDATE parts SET specs = CASE id {arms} END WHERE id IN ({holes})",
            params,
        )
        db._conn.commit()


def print_report(report: dict) -> None:
    print(f"{report['rows_updated']} of {report['rows_examined']} rows would change")
    for category in sorted(report["by_category"]):
        for key in sorted(report["by_category"][category]):
            counts = report["by_category"][category][key]
            print(f"  {category}.{key}: +{counts['gained']} -{counts['lost']}")


def main() -> int:
    resolve_target()  # refuses production Turso unless --yes-production;
                       # must run before Database() opens any connection
    apply = "--apply" in sys.argv[1:]
    db = Database()
    print(f"target: {db._target}   mode: {'APPLY' if apply else 'DRY RUN'}")

    rows = db._conn.execute("SELECT id, name, category, specs FROM parts").fetchall()
    updates, report = compute_backfill(
        (r["id"], r["name"], r["category"], r["specs"]) for r in rows
    )
    print_report(report)

    if apply:
        apply_updates(db, updates)
        print("applied")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
