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
    """
    The end-to-end shape of the bug: N well-formed products must produce N
    distinct rows, even when malformed ones are mixed into the same batch.

    The pre-fix `_slug` was substitution-only, with no path-length check and
    no blocklist: `re.sub(r"https?://[^/]+/", "", url)` strips exactly one
    "<scheme>://<host>/" prefix and nothing else. For
    "https://czone.com.pk/product/" and "https://czone.com.pk/product" (no
    trailing slash), that leaves "product/" and "product" respectively; both
    collapse to the same slug "product" after substitution. Two different
    dicts sharing `(source, source_id)` isn't a duplicate-row problem — it's
    an upsert collision: the second overwrites the first, so the batch
    silently produces one fewer row than expected (51 for 50 good + 2
    colliding, under the pre-fix code). The 50 well-formed, non-colliding
    URLs alone (the original version of this test) never exercised that path
    and would pass unchanged whether or not the fix existed.

    Post-fix, both of those URLs raise ValueError (blocklisted "product")
    and get quarantined instead of colliding — so the well-formed 50 stay
    fully distinct and uncorrupted, landing at exactly 50 rows.
    """
    db = Database(tmp_path / "t.db")
    good = [{
        "name": f"GPU {i}", "price_pkr": 50000 + i,
        "url": f"https://czone.com.pk/product/gpu-{i}",
        "category": "gpu", "source": "czone",
        "scraped_at": "2026-08-14T00:00:00Z",
    } for i in range(50)]
    colliding_under_old_slug = [
        {
            "name": "Phantom A", "price_pkr": 99999,
            "url": "https://czone.com.pk/product/",
            "category": "gpu", "source": "czone",
            "scraped_at": "2026-08-14T00:00:00Z",
        },
        {
            "name": "Phantom B", "price_pkr": 99998,
            "url": "https://czone.com.pk/product",
            "category": "gpu", "source": "czone",
            "scraped_at": "2026-08-14T00:00:00Z",
        },
    ]
    db.upsert_products(good + colliding_under_old_slug)
    assert db._conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 50
    names = {r[0] for r in db._conn.execute("SELECT name FROM parts").fetchall()}
    assert names == {f"GPU {i}" for i in range(50)}
    assert "Phantom A" not in names and "Phantom B" not in names
    db.close()
