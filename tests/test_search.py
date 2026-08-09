"""
Tests for market search: token-AND matching and LIKE-wildcard neutralisation.

Regression target: a single `name LIKE '%<whole query>%'` meant "5060 ti"
matched "RTX 5060 Ti White" but "5060 ti white" matched nothing, because the
three words are not contiguous in the retailer's product name.
"""
import json
import tempfile
from pathlib import Path

import pytest

from db.database import _like_escape, get_db, search_tokens


# ── tokeniser ────────────────────────────────────────────────────────────────

def test_like_escape_neutralises_wildcards():
    assert _like_escape("100%") == "100\\%"
    assert _like_escape("a_b") == "a\\_b"
    assert _like_escape("back\\slash") == "back\\\\slash"


# ── list_parts, end-to-end on a temp DB ──────────────────────────────────────

@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as d:
        database = get_db(Path(d) / "t.db")
        yield database
        database.close()


def _seed(db, name, category="gpu", price=100000):
    cur = db._conn.cursor()
    # name_norm is populated here (rather than relying on upsert_products) so
    # these tests can seed rows directly and still exercise list_parts()'s
    # real matching path against the same column production writes.
    pid = cur.execute(
        "INSERT INTO parts (source, source_id, name, category, url, specs, name_norm, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1) RETURNING id",
        ("test.pk", name, name, category, f"http://x/{name}", json.dumps({}), normalize_name(name)),
    ).fetchone()["id"]
    cur.execute(
        "INSERT INTO price_log (part_id, price_pkr, scraped_at) VALUES (?, ?, ?)",
        (pid, price, "2026-01-01T00:00:00Z"),
    )
    db._conn.commit()
    return pid


def _names(db, q):
    items, _ = db.list_parts(category="gpu", q=q)
    return sorted(i["name"] for i in items)


WHITE = "MSI GeForce RTX 5060 Ti Ventus 2X White 16GB"
BLACK = "Gigabyte GeForce RTX 5060 Ti Gaming OC 16GB"
PLAIN = "Zotac GeForce RTX 5060 Twin Edge 8GB"


def test_non_adjacent_words_match(db):
    """The reported bug: '5060 ti white' returned nothing."""
    _seed(db, WHITE)
    _seed(db, BLACK)
    _seed(db, PLAIN)
    assert _names(db, "5060 ti white") == [WHITE]

def test_all_tokens_required(db):
    _seed(db, WHITE)
    _seed(db, BLACK)
    _seed(db, PLAIN)
    # "ti" excludes the non-Ti card
    assert _names(db, "5060 ti") == [BLACK, WHITE]

def test_word_order_irrelevant(db):
    _seed(db, WHITE)
    assert _names(db, "white 5060") == [WHITE]
    assert _names(db, "ventus msi") == [WHITE]

def test_no_token_matches_nothing_extra(db):
    _seed(db, WHITE)
    assert _names(db, "5060 ti purple") == []

def test_case_insensitive(db):
    _seed(db, WHITE)
    assert _names(db, "MSI VENTUS") == [WHITE]

def test_single_token_still_works(db):
    _seed(db, WHITE)
    _seed(db, PLAIN)
    assert _names(db, "5060") == sorted([WHITE, PLAIN])

def test_short_token_does_not_match_inside_a_word(db):
    """
    'ti' must not match the 'ti' in 'Edition' — the substring version of this
    query returned every non-Ti card in the catalogue.
    """
    _seed(db, "Asus Dual GeForce RTX 5060 White OC Edition 8GB")
    _seed(db, WHITE)
    assert _names(db, "5060 ti") == [WHITE]

def test_token_matches_word_prefix(db):
    """Prefix matching keeps search-as-you-type usable mid-word."""
    _seed(db, WHITE)
    assert _names(db, "vent") == [WHITE]
    assert _names(db, "5060 ventu whi") == [WHITE]

def test_separators_normalised_on_both_sides(db):
    _seed(db, "Intel Core i5-13400F Desktop Processor - 20M Cache, LGA1700")
    assert len(_names(db, "13400f")) == 1
    assert len(_names(db, "i5-13400F")) == 1
    assert len(_names(db, "i5 13400")) == 1

def test_punctuation_only_query_is_not_a_wildcard(db):
    """Typing '%' used to match the whole catalogue via LIKE injection."""
    _seed(db, WHITE)
    _seed(db, PLAIN)
    # No usable tokens -> no name constraint, and critically not a wildcard
    # expansion of some *other* filter.
    assert _names(db, "%") == sorted([WHITE, PLAIN])

def test_literal_percent_in_name_is_searchable(db):
    _seed(db, "Corsair RM850x 80% Efficiency PSU")
    _seed(db, PLAIN)
    assert len(_names(db, "80")) == 1

def test_blank_query_returns_everything(db):
    _seed(db, WHITE)
    _seed(db, PLAIN)
    assert _names(db, "   ") == sorted([WHITE, PLAIN])

def test_total_count_matches_filtered_rows(db):
    _seed(db, WHITE)
    _seed(db, BLACK)
    _seed(db, PLAIN)
    items, total = db.list_parts(category="gpu", q="5060 ti white")
    assert total == 1
    assert len(items) == 1


# ── prebuilts search shares the tokeniser ────────────────────────────────────

def _seed_prebuilt(db, name):
    db._conn.execute(
        "INSERT INTO prebuilts (source, source_id, name, url, price_pkr, components, name_norm, scraped_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("test.pk", name, name, f"http://x/{name}", 150000, json.dumps({}), normalize_name(name), "2026-01-01T00:00:00Z"),
    )
    db._conn.commit()


def test_prebuilt_non_adjacent_words_match(db):
    _seed_prebuilt(db, "Starter 2.0 - Core i5 12400F & RTX 4060 Gaming PC")
    _seed_prebuilt(db, "Budget Build - Ryzen 5 5600 & RX 6600")
    items, total = db.list_prebuilts(q="starter rtx 4060")
    assert total == 1
    assert items[0]["name"].startswith("Starter 2.0")


import json
from pathlib import Path

from db.tokenize import MAX_SEARCH_TOKENS, normalize_name, search_tokens

_FIXTURE = Path(__file__).parent / "fixtures" / "tokenizer_cases.json"


def _fixture_cases():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _fixture_cases(), ids=lambda c: c["input"])
def test_tokenizer_matches_shared_fixture(case):
    assert search_tokens(case["input"]) == case["expected"]


def test_tokens_capped_at_max():
    assert len(search_tokens(" ".join(str(i) for i in range(50)))) == MAX_SEARCH_TOKENS


def test_normalize_name_pads_with_spaces():
    assert normalize_name("RTX 5060 Ti") == " rtx 5060 ti "


def test_normalize_name_splits_letter_digit_runs():
    assert normalize_name("MSI RTX5090 Gaming") == " msi rtx 5090 gaming "


def test_normalize_name_empty_is_single_space():
    assert normalize_name("%%%") == " "


def test_normalize_name_is_not_token_capped():
    # A 30-word product name must normalise in full; the cap applies to user
    # queries only. Capping here would make the tail of long names unsearchable.
    long_name = " ".join(f"word{i}" for i in range(30))
    assert normalize_name(long_name).count("word") == 30


def test_merged_token_query_matches_spaced_name(tmp_path):
    db = get_db(tmp_path / "t.db")
    db.upsert_products([{
        "name": "MSI GeForce RTX 5060 Ti Ventus 2X White 16GB",
        "price_pkr": 100000, "url": "https://x.pk/a", "category": "gpu",
        "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z",
    }])
    for query in ("5060 ti", "5060ti", "5060 ti white"):
        items, total = db.list_parts(q=query)
        assert total == 1, f"{query!r} returned {total}"
    db.close()


def test_merged_catalogue_name_matches_spaced_query(tmp_path):
    # The mirror case: retailer wrote "RTX5090", user types "5090".
    db = get_db(tmp_path / "t.db")
    db.upsert_products([{
        "name": "Gigabyte RTX5090 Windforce OC", "price_pkr": 900000,
        "url": "https://x.pk/b", "category": "gpu",
        "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z",
    }])
    items, total = db.list_parts(q="5090")
    assert total == 1
    db.close()


def test_short_token_still_anchors_at_word_start(tmp_path):
    db = get_db(tmp_path / "t.db")
    db.upsert_products([{
        "name": "Asus RTX 4070 OC Edition", "price_pkr": 200000,
        "url": "https://x.pk/c", "category": "gpu",
        "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z",
    }])
    # "ti" must not match the "ti" inside "Edition".
    _, total = db.list_parts(q="4070 ti")
    assert total == 0
    db.close()


def test_name_norm_is_populated_on_upsert(tmp_path):
    db = get_db(tmp_path / "t.db")
    db.upsert_products([{
        "name": "MSI RTX5090", "price_pkr": 900000, "url": "https://x.pk/d",
        "category": "gpu", "source": "czone.com.pk",
        "scraped_at": "2026-08-07T00:00:00Z",
    }])
    row = db._conn.execute("SELECT name_norm FROM parts").fetchone()
    assert row[0] == " msi rtx 5090 "
    db.close()


def test_name_norm_refreshed_when_name_changes(tmp_path):
    db = get_db(tmp_path / "t.db")
    # price_pkr must clear _MIN_PRICE["gpu"] (4000) or upsert_products()
    # silently skips the row and the table stays empty.
    base = {"price_pkr": 100000, "url": "https://x.pk/e", "category": "gpu",
            "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z"}
    db.upsert_products([{**base, "name": "Old Name"}])
    db.upsert_products([{**base, "name": "New RTX5090 Name"}])
    row = db._conn.execute("SELECT name_norm FROM parts").fetchone()
    assert row[0] == " new rtx 5090 name "
    db.close()


# ── list_parts(ids=...) — the client-index fetch path ─────────────────────────

def test_list_parts_by_ids_preserves_client_order(tmp_path):
    db = get_db(tmp_path / "t.db")
    base = {"category": "gpu", "source": "czone.com.pk",
            "scraped_at": "2026-08-07T00:00:00Z"}
    db.upsert_products([
        {**base, "name": "Card A", "price_pkr": 300000, "url": "https://x.pk/a"},
        {**base, "name": "Card B", "price_pkr": 100000, "url": "https://x.pk/b"},
        {**base, "name": "Card C", "price_pkr": 200000, "url": "https://x.pk/c"},
    ])
    all_ids = {r["name"]: r["id"] for r in db.list_parts()[0]}
    wanted = [all_ids["Card C"], all_ids["Card A"], all_ids["Card B"]]
    items, total = db.list_parts(ids=wanted)
    assert [i["id"] for i in items] == wanted   # NOT price order
    assert total == 3
    db.close()


def test_list_parts_by_ids_ignores_unknown_ids(tmp_path):
    db = get_db(tmp_path / "t.db")
    # price_pkr must clear _MIN_PRICE["gpu"] (4000) or upsert_products() silently
    # skips the row and the id lookup below has nothing to find.
    db.upsert_products([{
        "name": "Card A", "price_pkr": 5000, "url": "https://x.pk/a", "category": "gpu",
        "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z"}])
    real = db.list_parts()[0][0]["id"]
    items, total = db.list_parts(ids=[real, 99999999])
    assert total == 1 and len(items) == 1
    db.close()


def test_list_parts_empty_ids_returns_nothing(tmp_path):
    db = get_db(tmp_path / "t.db")
    db.upsert_products([{
        "name": "Card A", "price_pkr": 1, "url": "https://x.pk/a", "category": "gpu",
        "source": "czone.com.pk", "scraped_at": "2026-08-07T00:00:00Z"}])
    # An empty ID list means "the client matched nothing" — NOT "no filter".
    assert db.list_parts(ids=[]) == ([], 0)
    db.close()
