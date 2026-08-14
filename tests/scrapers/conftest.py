"""
Parser tests read saved retailer HTML, never the network.

Retailer markup changes without warning; a saved fixture is what turns "the
scraper broke last Friday" into "this test failed the moment I edited it".
"""
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "html"


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
