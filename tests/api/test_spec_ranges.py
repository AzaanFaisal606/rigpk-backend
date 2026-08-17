"""
A dropdown offering "16-32GB" must return everything in that range. Filtering
on one exact value silently drops most of what the label promises (M29).
"""
from db.database import Database


def test_filters_response_omits_capacity_range(client, seeded_db):
    """
    Fix round 2: `/api/parts/filters` must return exactly the key set
    `master` returns — no `capacity_range`. FilterBar.tsx builds its spec
    dropdowns generically from Object.entries(filterOptions), filtered only
    on non-empty values, not on a known-key allowlist. An additive
    `capacity_range` key therefore rendered as a real, mislabeled, dead
    dropdown on the live RAM market page (no SPEC_LABELS entry, and
    getParts()'s allow-list silently drops the param). This test fails if
    the key is reintroduced without the matching frontend work.
    """
    db = Database(seeded_db)
    db.upsert_products([
        {"name": f"Corsair Vengeance {cap}GB DDR5 6000MHz", "price_pkr": 20000 + cap,
         "url": f"https://czone.com.pk/product/ram-{cap}", "category": "ram",
         "source": "czone", "scraped_at": "2026-08-14T00:00:00Z"}
        for cap in (8, 16, 32, 64)
    ])
    db.close()

    options = client.get("/api/parts/filters?category=ram").json()

    # The deployed frontend's FilterBar.bucketValues() groups the raw value
    # list client-side — that shape must be unchanged. Only 16/32GB show up:
    # the RAM extractor only ever stores those two sizes (8/64GB rows above
    # get capacity=None and are excluded), per _extract_ram_capacity.
    assert set(options["capacity"]) == {"16GB", "32GB"}

    # Exactly master's key set for a fully-populated ram category — brand,
    # ddr_type and speed all come out of the same seeded name string.
    assert set(options.keys()) == {"brand", "ddr_type", "speed", "capacity"}
    assert "capacity_range" not in options


def test_bucket_filter_matches_the_whole_range(client, seeded_db):
    """
    The range predicate itself (list_parts / _parse_bucket) still works even
    though get_filter_options no longer advertises a bucket label to
    discover it by — Phase 4 adds the discovery surface, not the predicate.
    Driven directly through /api/parts?capacity=... rather than through the
    filters response, since that response no longer emits this shape.
    """
    db = Database(seeded_db)
    db.upsert_products([
        {"name": f"Corsair Vengeance {cap}GB DDR5 6000MHz", "price_pkr": 20000 + cap,
         "url": f"https://czone.com.pk/product/ram-{cap}", "category": "ram",
         "source": "czone", "scraped_at": "2026-08-14T00:00:00Z"}
        for cap in (8, 16, 32, 64)
    ])
    db.close()

    r = client.get("/api/parts?category=ram&capacity=16-32GB")
    names = [i["name"] for i in r.json()["items"]]
    assert any("16GB" in n for n in names)
    assert any("32GB" in n for n in names)
    assert not any("64GB" in n for n in names)
    assert not any("8GB" in n for n in names)
