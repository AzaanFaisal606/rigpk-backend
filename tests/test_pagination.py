"""
Paging through a list must show every row exactly once.

ORDER BY price with no tiebreaker leaves tied rows in engine-defined order,
which LIMIT/OFFSET is free to reshuffle between statements — so a row can
appear on two pages while another never appears at all. The live DB has 156
tied prices in `gpu` alone.
"""
import pytest

from db.database import Database


@pytest.fixture
def seeded(tmp_path):
    db = Database(tmp_path / "t.db")
    products = []
    for i in range(200):
        products.append({
            "name": f"Test GPU {i}",
            # 10 distinct prices across 200 rows => 20 rows tied at each price
            "price_pkr": 50000 + (i % 10) * 1000,
            "url": f"https://example.com/p/gpu-{i}",
            "category": "gpu",
            "source": "czone",
            "scraped_at": "2026-08-14T00:00:00Z",
        })
    db.upsert_products(products)
    yield db
    db.close()


def test_pagination_covers_every_row_exactly_once(seeded):
    seen = []
    for offset in range(0, 200, 50):
        rows, total = seeded.list_parts(category="gpu", limit=50, offset=offset)
        assert total == 200
        seen.extend(r["id"] for r in rows)
    assert len(seen) == 200
    assert len(set(seen)) == 200, "a row appeared on two pages"


def test_repeated_identical_query_is_stable(seeded):
    first, _ = seeded.list_parts(category="gpu", limit=50, offset=50)
    second, _ = seeded.list_parts(category="gpu", limit=50, offset=50)
    assert [r["id"] for r in first] == [r["id"] for r in second]


def test_sort_desc_orders_by_price(seeded):
    rows, _ = seeded.list_parts(category="gpu", sort="price_desc", limit=50)
    prices = [r["price_pkr"] for r in rows]
    assert prices == sorted(prices, reverse=True)


def test_ids_path_preserves_client_order(seeded):
    all_rows, _ = seeded.list_parts(category="gpu", limit=5)
    wanted = [r["id"] for r in all_rows][::-1]
    rows, _ = seeded.list_parts(ids=wanted)
    assert [r["id"] for r in rows] == wanted


def test_empty_ids_returns_nothing(seeded):
    rows, total = seeded.list_parts(ids=[])
    assert rows == [] and total == 0
