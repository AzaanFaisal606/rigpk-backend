"""
A dropdown offering "16-32GB" must return everything in that range. Filtering
on one exact value silently drops most of what the label promises (M29).
"""
from db.database import Database


def test_bucket_filter_matches_the_whole_range(client, seeded_db):
    db = Database(seeded_db)
    db.upsert_products([
        {"name": f"Corsair Vengeance {cap}GB DDR5 6000MHz", "price_pkr": 20000 + cap,
         "url": f"https://czone.com.pk/product/ram-{cap}", "category": "ram",
         "source": "czone", "scraped_at": "2026-08-14T00:00:00Z"}
        for cap in (8, 16, 32, 64)
    ])
    db.close()

    # Use a real bucket label straight from the filters endpoint, not an
    # invented one — get_filter_options and _parse_bucket must agree on shape.
    options = client.get("/api/parts/filters?category=ram").json()
    bucket_label = options["capacity"][0]

    r = client.get(f"/api/parts?category=ram&capacity={bucket_label}")
    names = [i["name"] for i in r.json()["items"]]
    assert any("16GB" in n for n in names)
    assert any("32GB" in n for n in names)
    assert not any("64GB" in n for n in names)
