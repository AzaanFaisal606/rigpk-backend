"""
Post-scrape anomaly detection.

The weekly job must react to more than a hard crash. A single retailer quietly
returning far fewer parts than last week, or one category inside an otherwise-OK
source dropping to zero, is exactly the kind of silent breakage we want a human
(or the self-heal action) to look at. This module turns those into an explicit
list of anomaly strings; the orchestrators print them and, under --strict (CI),
exit non-zero so the job goes red and self-heal fires.

Thresholds are deliberately sensitive and env-tunable:
    SCRAPE_DROP_THRESHOLD   fractional source-level drop that counts as suspicious
                            (default 0.50 = a >50% fall in active rows).
    SCRAPE_MIN_SOURCE_BASE  ignore drops on sources smaller than this (noise floor).
    SCRAPE_MIN_CAT_BASE     a category must have had at least this many rows before
                            for its fall to zero to count (ignore tiny categories).
"""

import os

DROP_THRESHOLD = float(os.getenv("SCRAPE_DROP_THRESHOLD", "0.50"))
MIN_SOURCE_BASELINE = int(os.getenv("SCRAPE_MIN_SOURCE_BASE", "20"))
MIN_CAT_BASELINE = int(os.getenv("SCRAPE_MIN_CAT_BASE", "5"))


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
) -> list[str]:
    """
    Categories that had rows before the run but zero after it — only for sources
    whose run was trusted (a stale source is already reported at source level, and
    its categories keep their old counts since it was never swept).
    """
    out = []
    for (src, cat), before in sorted(before_cat.items()):
        if src not in ok_sources or before < min_baseline:
            continue
        if after_cat.get((src, cat), 0) == 0:
            out.append(f"{src}/{cat}: {before} → 0 — category empty this run")
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
