import json
import urllib.error

import pytest

from db.database import Database
from scrapers.details.parse import parse_details
from scrapers.zah.html.details import extract_zah_raw, fetch_zah_details

SRC = "zahcomputers.pk"


def test_extract_gpu_page(load_details_fixture):
    raw = extract_zah_raw(load_details_fixture("zah_gpu_page.html"))
    d = parse_details(raw, "MSI GeForce RTX 5050 8G GAMING OC")
    assert len(d["highlights"]) == 12 and d["highlights"][1].startswith("8GB GDDR6 Memory:")
    assert len(d["specs"]) >= 20 and "Pakistan" not in json.dumps(d)


def test_extract_case_page(load_details_fixture):
    d = parse_details(extract_zah_raw(load_details_fixture("zah_case_page.html")))
    assert len(d["highlights"]) == 5 and d["specs"]


def test_extract_empty_page():
    raw = extract_zah_raw("<html></html>")
    assert raw == {"kind": "zah_page", "source": SRC, "highlights_html": "", "desc_html": ""}


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db", allow_remote_migrations=False)
    yield d
    d.close()


def _seed(db, n):
    db.upsert_products([
        {"name": f"Zah Card {i}", "price_pkr": 100000, "url": f"https://zahcomputers.pk/p/{i}",
         "category": "gpu", "source": SRC, "scraped_at": "2026-08-01T00:00:00Z"}
        for i in range(1, n + 1)
    ])


class Fake:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        o = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(o, BaseException):
            raise o
        return o


def _http(code):
    # what BaseScraper.fetch really raises for a 404: RuntimeError from HTTPError
    err = urllib.error.HTTPError("u", code, "x", {}, None)
    try:
        try:
            raise err
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Failed to fetch u: HTTP {code}") from e
    except RuntimeError as r:
        return r


def test_fetch_pass_writes_and_skips_404(db, load_details_fixture):
    _seed(db, 2)
    fake = Fake(load_details_fixture("zah_gpu_page.html"), _http(404))
    out = fetch_zah_details(db, limit=10, scraper=fake, sleep=lambda s: None)
    assert out == {"fetched": 1, "written": 1, "skipped": 1, "stopped": False}


def test_bare_httperror_410_skipped(db):
    _seed(db, 1)
    fake = Fake(urllib.error.HTTPError("u", 410, "gone", {}, None))
    out = fetch_zah_details(db, limit=10, scraper=fake, sleep=lambda s: None)
    assert out["skipped"] == 1 and out["stopped"] is False


def test_three_failures_stop(db):
    _seed(db, 5)
    fake = Fake(urllib.error.URLError("boom"))
    out = fetch_zah_details(db, limit=10, scraper=fake, sleep=lambda s: None)
    assert out == {"fetched": 0, "written": 0, "skipped": 0, "stopped": True}
    assert len(fake.calls) == 3
    assert len(db.parts_missing_details(SRC, 100)) == 5


def test_empty_page_still_stored(db):
    _seed(db, 1)
    out = fetch_zah_details(db, limit=10, scraper=Fake("<html></html>"), sleep=lambda s: None)
    assert out["written"] == 1
    assert db.parts_missing_details(SRC, 100) == []


def test_limit_respected(db):
    _seed(db, 3)
    fake = Fake("<html></html>")
    out = fetch_zah_details(db, limit=2, scraper=fake, sleep=lambda s: None)
    assert out["fetched"] == 2 and len(fake.calls) == 2
