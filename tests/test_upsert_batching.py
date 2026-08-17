"""
upsert_products() writes in chunked multi-row statements.

Against Turso every execute() is one network round trip, so the cost of a
scrape's write phase is the number of statements it issues, not the number of
rows they carry. The row-at-a-time version issued ~2 per product — 16,700 for
a full nine-source run — and spent 2h20m of a 2h25m run waiting on the wire.

These tests pin the two things batching can break that nothing downstream
would notice: the id-to-product mapping (a price silently attached to the
wrong part) and the in-batch duplicate handling (SQLite rejects a multi-row
upsert that names the same conflict target twice). The statement count itself
is pinned too, since a future edit that reintroduces a per-row execute would
otherwise pass every other test in the suite.
"""
import pytest

from db.database import _PARTS_CHUNK, _PRICE_CHUNK, Database


def _p(i, price=100000, at="2026-08-01T00:00:00Z", source="czone", category="gpu"):
    return {
        "name": f"Test Card {i}", "price_pkr": price,
        "url": f"https://example.com/p/card-{i}",
        "category": category, "source": source, "scraped_at": at,
    }


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


class _StatementCounter:
    """
    Counts statements actually handed to SQLite.

    sqlite3's trace callback fires per prepared statement, including the
    BEGIN/COMMIT the driver issues implicitly, so the assertions below use
    generous bounds — what matters is the order of magnitude, not the exact
    figure.
    """

    def __init__(self, conn):
        self.n = 0
        conn.set_trace_callback(self._on)

    def _on(self, _sql):
        self.n += 1


def test_price_belongs_to_its_own_part_across_chunk_boundaries(db):
    """
    The id map is built from RETURNING, whose row order SQLite does not
    promise matches VALUES order. Matching by position instead of by
    (source, source_id) would attach prices to the wrong products — well
    formed, plausible, and undetectable downstream. Prices here are unique
    per product so any mis-pairing shows up.
    """
    n = _PARTS_CHUNK * 2 + 37          # spans three parts chunks, unaligned
    db.upsert_products([_p(i, price=100000 + i) for i in range(n)])

    rows = db._conn.execute(
        """
        SELECT p.source_id, p.latest_price, pl.price_pkr
        FROM parts p JOIN price_log pl ON pl.part_id = p.id
        """
    ).fetchall()
    assert len(rows) == n
    for r in rows:
        expected = 100000 + int(r["source_id"].rsplit("-", 1)[-1])
        assert r["price_pkr"] == expected, f"{r['source_id']} got price {r['price_pkr']}"
        assert r["latest_price"] == expected


def test_price_rows_span_their_own_chunk_boundary(db):
    """Same guarantee, forced across a price_log chunk rather than a parts one."""
    n = _PRICE_CHUNK + 11
    db.upsert_products([_p(i, price=200000 + i) for i in range(n)])
    assert db._conn.execute("SELECT COUNT(*) c FROM price_log").fetchone()["c"] == n


def test_same_product_twice_in_one_batch_does_not_raise(db):
    """
    One product legitimately appears under two category URLs at several
    retailers. SQLite rejects a multi-row INSERT ... ON CONFLICT DO UPDATE
    whose VALUES list names the same conflict target twice, so phase 1 has
    to fold them first — this is the case that made de-duplication mandatory
    rather than merely tidy.
    """
    dupe = _p(1, price=100000)
    again = {**_p(1, price=120000), "name": "Test Card 1 (renamed)"}
    inserted = db.upsert_products([dupe, again])

    assert db._conn.execute("SELECT COUNT(*) c FROM parts").fetchone()["c"] == 1
    assert inserted == 1
    row = db._conn.execute("SELECT name, latest_price FROM parts").fetchone()
    # Last occurrence wins, matching what the row-at-a-time version left
    # behind once its second DO UPDATE had overwritten the first.
    assert row["name"] == "Test Card 1 (renamed)"
    assert row["latest_price"] == 120000


def test_duplicate_in_batch_logs_the_winning_price(db):
    """
    The row-at-a-time version disagreed with itself here: parts.latest_price
    took the second occurrence (DO UPDATE) while price_log kept the first
    (the second hit UNIQUE and was swallowed). Batching resolves both from
    the same folded row, so the cache and the history now agree.
    """
    db.upsert_products([_p(1, price=100000), _p(1, price=120000)])
    prices = [r["price_pkr"] for r in
              db._conn.execute("SELECT price_pkr FROM price_log").fetchall()]
    assert prices == [120000]
    assert db._conn.execute("SELECT latest_price FROM parts").fetchone()["latest_price"] == 120000


def test_rerunning_the_same_scrape_inserts_no_price_rows(db):
    """
    ON CONFLICT(part_id, scraped_at) DO NOTHING replaced a per-row
    IntegrityError catch. The returned count must still be the number of
    rows genuinely written, which is what the scrape report prints.
    """
    batch = [_p(i) for i in range(5)]
    assert db.upsert_products(batch) == 5
    assert db.upsert_products(batch) == 0
    assert db._conn.execute("SELECT COUNT(*) c FROM price_log").fetchone()["c"] == 5


def test_a_second_scrape_at_a_new_time_still_logs(db):
    batch = [_p(i) for i in range(5)]
    db.upsert_products(batch)
    assert db.upsert_products([_p(i, price=90000, at="2026-08-08T00:00:00Z")
                               for i in range(5)]) == 5
    assert db._conn.execute("SELECT COUNT(*) c FROM price_log").fetchone()["c"] == 10


def test_seen_ids_cover_every_upserted_part(db):
    """
    deactivate_unseen_parts() sweeps everything absent from _last_seen_ids.
    A part whose id failed to come back from RETURNING would be delisted
    moments after being scraped, so the set must be complete.
    """
    n = _PARTS_CHUNK + 5
    db.upsert_products([_p(i) for i in range(n)])
    assert len(db._last_seen_ids["czone"]) == n
    assert db.deactivate_unseen_parts("czone") == 0


def test_two_sources_keep_separate_seen_id_sets(db):
    db.upsert_products([_p(i, source="czone") for i in range(3)]
                       + [_p(i, source="pakbyte") for i in range(4)])
    assert len(db._last_seen_ids["czone"]) == 3
    assert len(db._last_seen_ids["pakbyte"]) == 4


def test_statement_count_scales_with_chunks_not_products(db):
    """
    The whole point of the change. 600 products cost ~1,200 statements
    before; the bound here is deliberately loose (it still admits ~10x the
    expected count) so it fails only on a genuine return to per-row writes,
    not on an added statement or a retuned chunk size.
    """
    n = 600
    counter = _StatementCounter(db._conn)
    db.upsert_products([_p(i) for i in range(n)])
    assert counter.n < 50, f"{counter.n} statements for {n} products — batching regressed"


def test_quarantined_duplicates_count_every_rejection(db):
    """
    times_rejected exists to make an over-broad blocklist term discoverable,
    so folding in-batch duplicates into one row must not lose the count.
    """
    bad = {**_p(1, price=100), "name": "Cheap GPU"}      # under the gpu floor
    db.upsert_products([bad, bad, bad])
    row = db._conn.execute(
        "SELECT times_rejected FROM quarantined_rows"
    ).fetchone()
    assert row["times_rejected"] == 3


def test_quarantine_count_accumulates_across_calls(db):
    bad = {**_p(1, price=100), "name": "Cheap GPU"}
    db.upsert_products([bad, bad])
    db.upsert_products([bad])
    assert db._conn.execute(
        "SELECT times_rejected FROM quarantined_rows"
    ).fetchone()["times_rejected"] == 3


def test_a_batch_of_only_rejects_writes_nothing_and_returns_zero(db):
    assert db.upsert_products([{**_p(1, price=100), "name": "Cheap GPU"}]) == 0
    assert db._conn.execute("SELECT COUNT(*) c FROM parts").fetchone()["c"] == 0


def test_empty_batch_is_a_no_op(db):
    assert db.upsert_products([]) == 0
