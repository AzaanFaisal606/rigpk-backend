"""
G4: Phase 2 raised motherboard form_factor extraction from 0% to 63.3%
(scrapers/spec_extractor.py _extract_form_factor(), wired in at upsert time
for category == "motherboard"), but _CATEGORY_SPEC_KEYS["motherboard"] never
listed "form_factor" — so it never reached /api/parts/filters and never
became a usable filter anywhere, despite the extraction work being live.

Confirmed safe to add before writing this: form_factor already has a
SPEC_LABELS entry ("Form", frontend/lib/constants.ts) and is already in
getParts()'s query-param allow-list (frontend/lib/api.ts) — both from the
"case" category's existing form_factor filter — so exposing it for
motherboard reuses already-wired frontend support instead of shipping a new
dead control.
"""
from db.database import get_db


def test_motherboard_form_factor_reaches_filter_options(tmp_path):
    db = get_db(tmp_path / "t.db")
    db.upsert_products([
        {"name": "ASUS ROG Strix B650-A Micro-ATX", "price_pkr": 45000,
         "url": "https://x.pk/a", "category": "motherboard", "source": "czone.com.pk",
         "scraped_at": "2026-08-15T00:00:00Z"},
        {"name": "MSI MAG B650 Tomahawk ATX", "price_pkr": 55000,
         "url": "https://x.pk/b", "category": "motherboard", "source": "czone.com.pk",
         "scraped_at": "2026-08-15T00:00:00Z"},
    ])

    options = db.get_filter_options("motherboard")
    db.close()

    assert "form_factor" in options
    assert set(options["form_factor"]) == {"Micro-ATX", "ATX"}
