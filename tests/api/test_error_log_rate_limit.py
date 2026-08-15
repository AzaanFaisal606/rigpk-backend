"""
G3: /api/stats is public and .github/workflows/keepwarm.yml pings it every
10 minutes on top of real traffic. `_safe_error()` used to `logger.error` the
full, pre-redaction error text on every single call — so while a source
stayed broken, get_stats() re-emitted that full detail on every request
(~1000 lines/week of exactly the content the classifier exists to keep off
the public response). It must now log a given source's error text at most
once per window, but still log immediately when the error text changes.
"""
import logging

import pytest

from db.database import Database


@pytest.fixture(autouse=True)
def _reset_error_log_cache():
    from backend.routers.parts import _last_logged_source_error

    _last_logged_source_error.clear()
    yield
    _last_logged_source_error.clear()


def _record_broken_run(db_path, source, error):
    db = Database(db_path)
    db.record_scrape_run(
        source,
        started_at="2026-08-15T00:00:00Z",
        finished_at="2026-08-15T00:05:00Z",
        ok=False,
        error=error,
    )
    db.close()


def _full_detail_records(caplog):
    return [
        r for r in caplog.records
        if "full detail, pre-redaction" in r.getMessage()
    ]


def test_repeated_requests_same_error_log_once(client, seeded_db, caplog):
    _record_broken_run(
        seeded_db, "czone",
        "RuntimeError: Failed to fetch https://czone.com.pk/gpu: HTTP 403",
    )

    with caplog.at_level(logging.ERROR, logger="backend.routers.parts"):
        for _ in range(5):
            resp = client.get("/api/stats")
            assert resp.status_code == 200

    records = _full_detail_records(caplog)
    assert len(records) == 1, (
        f"expected exactly one full-detail log line across 5 identical "
        f"requests, got {len(records)}"
    )


def test_different_error_still_logs(client, seeded_db, caplog):
    with caplog.at_level(logging.ERROR, logger="backend.routers.parts"):
        _record_broken_run(
            seeded_db, "czone",
            "TimeoutError: read of https://czone.com.pk/gpu exceeded READ_DEADLINE=30s",
        )
        r1 = client.get("/api/stats")
        assert r1.status_code == 200

        _record_broken_run(
            seeded_db, "czone",
            "HostBlocked: czone.com.pk returned 429 on 5 consecutive fetches",
        )
        r2 = client.get("/api/stats")
        assert r2.status_code == 200

    records = _full_detail_records(caplog)
    assert len(records) == 2, (
        f"a changed error for the same source must log again immediately, "
        f"got {len(records)} log lines"
    )
