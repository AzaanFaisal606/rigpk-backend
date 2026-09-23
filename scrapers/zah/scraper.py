"""
zahcomputers.pk scraper — WooCommerce Store API (see scrapers/woo/base.py)

zah is why the parts scrapers moved to the API: Cloudflare 403s every HTML
page from datacenter IPs (so every CI run), but not /wp-json/*.

Usage:
    python -m scrapers.zah.scraper          # GPU only (smoke test, no DB write)
    python -m scrapers.zah.scraper --all    # all categories
"""

from scrapers.listing_scraper import run_listing_cli
from scrapers.woo.base import WooStoreScraper


class ZahScraper(WooStoreScraper):
    SOURCE = "zahcomputers.pk"
    BASE = "https://zahcomputers.pk"
    # (product_cat slug, our category)
    CATEGORIES = [
        ("graphics-cards",      "gpu"),
        ("processors",          "cpu"),
        ("motherboard-chipset", "motherboard"),
        ("memory-module-ram",   "ram"),
        ("sata-ssd",            "ssd"),
        ("power-supplies",      "psu"),
        ("casing",              "case"),
        ("cooling-solutions",   "cooling"),
        ("monitors",            "monitor"),
    ]


def main():
    """Standalone smoke test — does NOT write to DB."""
    run_listing_cli(ZahScraper, ZahScraper.CATEGORIES, lambda slug: slug, write_db=False, default_filter="gpu")


if __name__ == "__main__":
    main()
