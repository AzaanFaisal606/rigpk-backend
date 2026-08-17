"""
Save a retailer page as a test fixture.

Uses the project's own fetch path so the saved page is exactly what the scraper
sees — same UA, same headers. Fetching with curl or a browser produces different
markup on the sites that run a bot challenge.

Usage: python scripts/save_fixture.py <url> <fixture-name.html>
"""
import sys
from pathlib import Path

from scrapers.base_scraper import BaseScraper

DEST = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "html"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    url, name = sys.argv[1], sys.argv[2]

    class _Fetcher(BaseScraper):
        def scrape(self, url: str):
            return []

    html = _Fetcher().fetch(url)
    DEST.mkdir(parents=True, exist_ok=True)
    out = DEST / name
    out.write_text(html, encoding="utf-8")
    print(f"saved {len(html)} bytes to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
