"""
parts is keyed on (source, source_id). A source_id that is constant or empty
collapses an entire catalogue into one row — 2,589 products becoming 1 is a
data-loss event that looks like a successful scrape.
"""
import pytest

from db.database import Database, _slug


def test_distinct_urls_give_distinct_slugs():
    urls = [
        "https://czone.com.pk/product/asus-rtx-4060",
        "https://czone.com.pk/product/asus-rtx-4070",
        "https://czone.com.pk/product/asus-rtx-4080",
    ]
    slugs = {_slug(u) for u in urls}
    assert len(slugs) == 3


@pytest.mark.parametrize("bad", ["", "not-a-url", "https://", "https://czone.com.pk/"])
def test_underivable_url_raises(bad):
    with pytest.raises(ValueError):
        _slug(bad)


def test_upsert_of_many_products_creates_many_rows(tmp_path):
    """The end-to-end shape of the bug: N products must produce N rows."""
    db = Database(tmp_path / "t.db")
    db.upsert_products([{
        "name": f"GPU {i}", "price_pkr": 50000 + i,
        "url": f"https://czone.com.pk/product/gpu-{i}",
        "category": "gpu", "source": "czone",
        "scraped_at": "2026-08-14T00:00:00Z",
    } for i in range(50)])
    assert db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 50
    db.close()
