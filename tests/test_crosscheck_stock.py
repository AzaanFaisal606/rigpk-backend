"""scripts/crosscheck_stock.compare: flag a parser that lists sold-out stock or
stores the wrong price. Pure function, no network."""
from scripts.crosscheck_stock import compare


def test_clean_source_has_no_drift():
    db = {f"p{i}": 1000 for i in range(20)}
    feed = {f"p{i}": (True, 1000) for i in range(20)}
    assert compare("x", db, feed).drift == []


def test_sold_out_rows_are_flagged():
    # the pakbyte bug: sold-out cards kept listed at their old price
    db = {f"p{i}": 1000 for i in range(20)}
    feed = {f"p{i}": (i >= 5, 1000) for i in range(20)}
    r = compare("pakbyte.pk", db, feed)
    assert len(r.sold_out) == 5
    assert r.drift and "sold out" in r.drift[0]


def test_crossed_out_prices_are_flagged():
    # the techmatched bug: the <del> price stored for every sale item
    db = {f"p{i}": 99000 for i in range(536)}
    feed = {f"p{i}": (True, 94999 if i < 14 else 99000) for i in range(536)}
    r = compare("techmatched.pk", db, feed)
    assert len(r.price_off) == 14
    assert any("different price" in d for d in r.drift)


def test_rows_missing_from_the_feed_are_ignored():
    r = compare("x", {"a": 1, "b": 2}, {"a": (True, 1)})
    assert (r.active, r.matched) == (2, 1)


def test_a_few_repricings_are_not_drift():
    db = {f"p{i}": 1000 for i in range(100)}
    feed = {f"p{i}": (True, 1100 if i < 4 else 1000) for i in range(100)}
    assert compare("x", db, feed).drift == []


def test_a_source_whose_last_scrape_failed_is_skipped(tmp_path, monkeypatch, capsys):
    # zah: blocked in CI, so its rows are weeks old and sold-out drift is expected
    from db.database import get_db
    from scripts import crosscheck_stock as cc

    path = tmp_path / "t.db"
    with get_db(path, allow_remote_migrations=False) as db:
        db.record_scrape_run("zahcomputers.pk", started_at="2026-09-18T00:00:00+00:00",
                             error="host blocked")
        db.record_scrape_run("pakbyte.pk", started_at="2026-09-18T00:00:00+00:00", ok=True)
    monkeypatch.setenv("DB_PATH", str(path))
    fetched = []
    monkeypatch.setattr(cc, "_woo_feed", lambda f, base: fetched.append(base) or {})
    monkeypatch.setattr(cc, "_shopify_feed", lambda f, base: fetched.append(base) or {})

    assert cc.main(["zahcomputers.pk", "pakbyte.pk", "--strict"]) == 0
    assert fetched == ["https://www.pakbyte.pk"]
    assert "zahcomputers.pk: skipped" in capsys.readouterr().out
