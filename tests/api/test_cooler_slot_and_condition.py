"""
The builder's cooler slot shares `cooling` with case fans, so the picker asks
for `exclude_type=Fan/Accessory`. Every row also carries `condition`, so a used
card can't pass for new.
"""
from db.database import Database


def _seed(path, rows):
    db = Database(path)
    db.upsert_products([
        {"name": name, "price_pkr": 20000 + i, "url": f"https://czone.com.pk/p/{i}",
         "category": cat, "source": "czone", "scraped_at": "2026-08-14T00:00:00Z"}
        for i, (name, cat) in enumerate(rows)
    ])
    db.close()


def test_exclude_type_drops_fans_but_keeps_untyped_coolers(client, seeded_db):
    _seed(seeded_db, [
        ("DeepCool LT720 360mm CPU Liquid Cooler", "cooling"),
        ("Corsair RS120 ARGB 120mm PWM Fan (Black, 3-Pack)", "cooling"),
        ("Thermalright Peerless Assassin 120 MINI", "cooling"),
    ])
    r = client.get("/api/parts?category=cooling&exclude_type=Fan/Accessory").json()
    names = {p["name"] for p in r["items"]}
    assert "Corsair RS120 ARGB 120mm PWM Fan (Black, 3-Pack)" not in names
    assert {"DeepCool LT720 360mm CPU Liquid Cooler",
            "Thermalright Peerless Assassin 120 MINI"} <= names


def test_condition_is_returned_without_specs(client, seeded_db):
    _seed(seeded_db, [
        ("Zotac RTX 3070 Twin Edge 8GB – USED", "gpu"),
        ("Zotac RTX 5070 Twin Edge 12GB", "gpu"),
    ])
    r = client.get("/api/parts?category=gpu&include_specs=false").json()
    by_name = {p["name"]: p["condition"] for p in r["items"]}
    assert by_name["Zotac RTX 3070 Twin Edge 8GB – USED"] == "Used"
    assert by_name["Zotac RTX 5070 Twin Edge 12GB"] is None
