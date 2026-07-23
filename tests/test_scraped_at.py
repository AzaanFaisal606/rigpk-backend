"""scraped_at_now(): normal UTC vs the SCRAPE_AS_OF_DATE bucket override."""

import re

from scrapers.base_scraper import scraped_at_now


def test_default_is_utc_now(monkeypatch):
    monkeypatch.delenv("SCRAPE_AS_OF_DATE", raising=False)
    ts = scraped_at_now()
    # ISO-8601 with a +00:00 offset (UTC).
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)
    assert ts.endswith("+00:00")


def test_override_forces_date_keeps_time(monkeypatch):
    monkeypatch.setenv("SCRAPE_AS_OF_DATE", "2026-07-24")
    ts = scraped_at_now()
    # Date part is pinned; the bucket is substr(scraped_at, 1, 10).
    assert ts[:10] == "2026-07-24"
    # Time-of-day is still real (non-zero clock component present).
    assert "T" in ts and ts.endswith("+00:00")


def test_malformed_override_ignored(monkeypatch):
    monkeypatch.setenv("SCRAPE_AS_OF_DATE", "not-a-date")
    ts = scraped_at_now()
    # Falls back to real now(), not corrupted.
    assert re.match(r"^\d{4}-\d{2}-\d{2}T", ts)
    assert not ts.startswith("not-a-date")
