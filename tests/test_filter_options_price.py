"""
G2: get_filter_options() only filtered on is_active, unlike list_parts()'s
`latest_price IS NOT NULL AND is_active = 1`. A part with no latest_price
could advertise a spec value in a filter dropdown that then matched zero
rows once list_parts() applied its own predicate.
"""
import pytest

from db.database import get_db


@pytest.fixture
def db(tmp_path):
    d = get_db(tmp_path / "t.db")
    d.upsert_products([
        {"name": "Gigabyte RTX 5090 Gaming", "price_pkr": 900000, "url": "https://x.pk/a",
         "category": "gpu", "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z"},
        {"name": "Sapphire RX 9070 Pulse", "price_pkr": 400000, "url": "https://x.pk/b",
         "category": "gpu", "source": "pakbyte.pk", "scraped_at": "2026-08-07T00:00:00Z"},
    ])
    yield d
    d.close()


def test_null_latest_price_excluded_from_filter_options(db):
    db._conn.execute(
        "UPDATE parts SET latest_price = NULL WHERE name LIKE '%9070%'"
    )
    db._conn.commit()

    options = db.get_filter_options("gpu")
    assert options.get("brand") == ["Gigabyte"], options.get("brand")
