"""
Parser tests read saved retailer HTML, never the network.

Retailer markup changes without warning; a saved fixture is what turns "the
scraper broke last Friday" into "this test failed the moment I edited it".
"""
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html"


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """
    No scraper test may touch the network. A test that calls the real
    .fetch() should fail loudly, not hang or silently hit czone.com.pk.
    """
    import urllib.request

    def _blocked(*args, **kwargs):
        raise RuntimeError("network blocked in tests")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)


@pytest.fixture
def load_fixture():
    def _load(name: str) -> str:
        path = FIXTURES / name
        if not path.exists():
            raise AssertionError(
                f"Missing fixture {name}. Save one with:\n"
                f"  python scripts/save_fixture.py <url> {name}"
            )
        return path.read_text(encoding="utf-8", errors="replace")
    return _load
