"""
Post-scrape anomaly detection.

The weekly job must react to more than a hard crash. A single retailer quietly
returning far fewer parts than last week, or one category inside an otherwise-OK
source dropping to zero, is exactly the kind of silent breakage we want a human
(or the self-heal action) to look at. This module turns those into an explicit
list of anomaly strings; the orchestrators print them and, under --strict (CI),
exit non-zero so the job goes red and self-heal fires.

Thresholds are deliberately sensitive and env-tunable:
    SCRAPE_DROP_THRESHOLD      fractional source-level drop that counts as suspicious
                               (default 0.50 = a >50% fall in active rows).
    SCRAPE_MIN_SOURCE_BASE     ignore drops on sources smaller than this (noise floor).
    SCRAPE_MIN_CAT_BASE        a category must have had at least this many rows before
                               for its fall to count (ignore tiny categories).
    SCRAPE_CAT_DROP_THRESHOLD  fractional per-category drop that counts as suspicious
                               (default 0.40). Lower than the source-level threshold on
                               purpose: a category can bleed out while the source total
                               still looks fine (the techmatched amputation — 653 -> 254
                               -> 163 — never emptied a category, it just kept shrinking).
    SCRAPE_MIN_PREBUILT_SOURCE_BASE  source-level noise floor for prebuilt sources, which
                               run far smaller than part sources (redtech has 13 total).
                               Deliberately lower than SCRAPE_MIN_SOURCE_BASE so a small
                               prebuilt source isn't exempted from ever being flagged.
"""

import os

DROP_THRESHOLD = float(os.getenv("SCRAPE_DROP_THRESHOLD", "0.50"))
MIN_SOURCE_BASELINE = int(os.getenv("SCRAPE_MIN_SOURCE_BASE", "20"))
MIN_CAT_BASELINE = int(os.getenv("SCRAPE_MIN_CAT_BASE", "5"))
CAT_DROP_THRESHOLD = float(os.getenv("SCRAPE_CAT_DROP_THRESHOLD", "0.40"))
MIN_PREBUILT_SOURCE_BASELINE = int(os.getenv("SCRAPE_MIN_PREBUILT_SOURCE_BASE", "5"))


def source_anomalies(
    source: str,
    before: int,
    after: int,
    ok: bool,
    error: str | None,
    *,
    drop: float = DROP_THRESHOLD,
    floor: int = MIN_SOURCE_BASELINE,
) -> list[str]:
    """Anomalies for one source: an outright failed run, or a suspicious drop."""
    if not ok:
        return [f"{source}: run failed — {error or 'unknown error'}"]
    if before >= floor and after < before * (1 - drop):
        pct = (after - before) / before * 100
        return [f"{source}: active {before} → {after} ({pct:+.0f}%) — suspicious drop"]
    return []


def category_anomalies(
    before_cat: dict[tuple[str, str], int],
    after_cat: dict[tuple[str, str], int],
    ok_sources: set[str],
    *,
    min_baseline: int = MIN_CAT_BASELINE,
    drop: float = CAT_DROP_THRESHOLD,
) -> list[str]:
    """
    Categories that emptied OR shrank sharply, for trusted runs only.

    A stale source is already reported at source level, and its categories keep
    their old counts since it was never swept — skip them here or every failed
    run would double-report at the category level too.

    Emptying was the only rule until 2026-08. It never fired on the failure that
    actually happens: a category that keeps shrinking run over run while the
    source as a whole still looks healthy (techmatched went 653 -> 254 -> 163
    without ever touching zero).
    """
    out = []
    for (src, cat), before in sorted(before_cat.items()):
        if src not in ok_sources or before < min_baseline:
            continue
        after = after_cat.get((src, cat), 0)
        if after == 0:
            out.append(f"{src}/{cat}: {before} → 0 — category empty this run")
        elif after < before * (1 - drop):
            pct = (after - before) / before * 100
            out.append(f"{src}/{cat}: {before} → {after} ({pct:+.0f}%) — suspicious drop")
    return out


def evaluate_parts(runs, before_src, after_src, before_cat, after_cat) -> list[str]:
    """Full parts-run verdict: per-source anomalies then per-category ones."""
    anomalies: list[str] = []
    ok_sources = {r["source"] for r in runs if r["ok"]}
    for r in runs:
        anomalies += source_anomalies(
            r["source"],
            before_src.get(r["source"], 0),
            after_src.get(r["source"], 0),
            r["ok"],
            r.get("error"),
        )
    anomalies += category_anomalies(before_cat, after_cat, ok_sources)
    return anomalies
