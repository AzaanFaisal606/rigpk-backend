"""Tests for scrapers/health.py anomaly detection."""

from scrapers import health


def test_ok_source_no_anomaly():
    assert health.source_anomalies("czone.com.pk", 200, 205, ok=True, error=None) == []


def test_failed_source_flagged():
    out = health.source_anomalies("amdhouse.pk", 237, 237, ok=False, error="429")
    assert len(out) == 1 and "run failed" in out[0] and "429" in out[0]


def test_suspicious_drop_flagged():
    # 200 -> 80 is a 60% fall, past the 50% default.
    out = health.source_anomalies("pakbyte.pk", 200, 80, ok=True, error=None)
    assert len(out) == 1 and "suspicious drop" in out[0]


def test_moderate_drop_ignored():
    # 200 -> 120 is a 40% fall, under the 50% threshold — not flagged.
    assert health.source_anomalies("pakbyte.pk", 200, 120, ok=True, error=None) == []


def test_tiny_source_below_floor_ignored():
    # Below MIN_SOURCE_BASELINE (20) — a big % swing on a handful of rows is noise.
    assert health.source_anomalies("small.pk", 10, 2, ok=True, error=None) == []


def test_category_emptied_flagged():
    before = {("czone.com.pk", "gpu"): 40, ("czone.com.pk", "cpu"): 30}
    after = {("czone.com.pk", "cpu"): 31}  # gpu vanished
    out = health.category_anomalies(before, after, ok_sources={"czone.com.pk"})
    assert len(out) == 1 and "czone.com.pk/gpu" in out[0]


def test_category_below_baseline_ignored():
    before = {("czone.com.pk", "cooling"): 3}  # under MIN_CAT_BASELINE (5)
    after: dict = {}
    assert health.category_anomalies(before, after, ok_sources={"czone.com.pk"}) == []


def test_category_skipped_for_stale_source():
    # A stale source is reported at source level; its categories keep old counts
    # and must not double-report.
    before = {("amdhouse.pk", "gpu"): 40}
    after: dict = {}
    assert health.category_anomalies(before, after, ok_sources=set()) == []


def test_evaluate_parts_combines():
    runs = [
        {"source": "czone.com.pk", "ok": True, "error": None},
        {"source": "amdhouse.pk", "ok": False, "error": "429"},
    ]
    before_src = {"czone.com.pk": 200, "amdhouse.pk": 237}
    after_src = {"czone.com.pk": 205, "amdhouse.pk": 237}
    before_cat = {("czone.com.pk", "gpu"): 40}
    after_cat: dict = {}  # czone gpu emptied
    out = health.evaluate_parts(runs, before_src, after_src, before_cat, after_cat)
    joined = " | ".join(out)
    assert "amdhouse.pk: run failed" in joined
    assert "czone.com.pk/gpu" in joined


# --- proportional category drop (not just fall-to-zero) ---
#
# Health rules must catch partial loss, not only total loss.
#
# The amputation that motivated this (techmatched 653 -> 254 -> 163) never
# emptied a category — it just kept shrinking, and nothing fired.

from scrapers.health import category_anomalies, source_anomalies


def test_category_halving_is_an_anomaly():
    out = category_anomalies(
        {("techmatched", "gpu"): 200},
        {("techmatched", "gpu"): 90},
        {"techmatched"},
    )
    assert out and "gpu" in out[0]


def test_small_but_total_loss_on_a_tiny_source_still_fires():
    """A 13-row prebuilt source losing 12 rows is 92% — size must not excuse it."""
    out = category_anomalies(
        {("redtech", "prebuilt"): 13},
        {("redtech", "prebuilt"): 1},
        {"redtech"},
        min_baseline=5,
    )
    assert out


def test_normal_churn_is_not_an_anomaly():
    out = category_anomalies(
        {("pakbyte", "gpu"): 200},
        {("pakbyte", "gpu"): 188},
        {"pakbyte"},
    )
    assert out == []


def test_growth_is_never_an_anomaly():
    out = category_anomalies(
        {("pakbyte", "gpu"): 100},
        {("pakbyte", "gpu"): 240},
        {"pakbyte"},
    )
    assert out == []


def test_untrusted_source_is_not_double_reported():
    """A failed source is already reported at source level; its categories are stale, not lost."""
    out = category_anomalies(
        {("czone", "gpu"): 200},
        {("czone", "gpu"): 0},
        ok_sources=set(),
    )
    assert out == []
