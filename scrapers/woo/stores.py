"""
The five smaller WooCommerce retailers, on the Store API (see woo/base.py).
zah has its own module, scrapers/zah/scraper.py.

Category slugs are the last path segment of each shop category URL.

Usage:
    python -m scrapers.woo.stores amd           # GPU only (smoke test, no DB write)
    python -m scrapers.woo.stores amd --all     # all categories
"""

import re
import sys

from scrapers.listing_scraper import run_listing_cli
from scrapers.woo.base import WooStoreScraper


# Shop filler that isn't part of the product: a list number ("02. Tracer X2")
# and the warranty ("New in 10 Months Warranty", "10months pump wty"). "used"
# is kept, since the condition spec reads it.
_LIST_NUMBER_RE = re.compile(r'^\d{1,2}\.\s*')
_WARRANTY_RE = re.compile(
    r'[\s,;–-]*\b(?:new\s+)?(?:in|with)?\s*\d+\s*(?:months?|weeks?|years?|yrs?)\s+'
    r'(?:pump\s+)?(?:warranty|wty)\b',
    re.IGNORECASE,
)


def clean_amd_name(name: str) -> str:
    name = _LIST_NUMBER_RE.sub('', name.strip())
    name = _WARRANTY_RE.sub('', name)
    return re.sub(r'\s{2,}', ' ', name).strip(' ,-–')


class AmdHouseScraper(WooStoreScraper):
    SOURCE = "amdhouse.pk"
    BASE = "https://amdhouse.pk"
    # amdhouse splits its catalogue into flat sibling categories rather than a
    # parent/child tree: "intel-motherboards" is not under "motherboards", and
    # the two share no products. Several of ours map many slugs to one category.
    # A retired slug returns [] (not a 404), so it costs one cheap request.
    CATEGORIES = [
        ("graphics-cards",          "gpu"),
        ("itx-graphics-cards",      "gpu"),
        ("processors",              "cpu"),
        ("intel-processors",        "cpu"),
        ("motherboards",            "motherboard"),
        ("intel-motherboards",      "motherboard"),
        ("itx-motherboards",        "motherboard"),
        ("ram",                     "ram"),
        ("storage-devices",         "ssd"),      # covers SSD + HDD
        ("pc-power-supplies",       "psu"),
        ("itx-psu",                 "psu"),
        ("pc-cases",                "case"),
        ("itx-pc-cases",            "case"),
        ("cpu-cooler",              "cooling"),
        ("low-profile-cpu-coolers", "cooling"),
        ("monitors",                "monitor"),
    ]

    def clean_name(self, name: str) -> str:
        return clean_amd_name(name)


class RbtScraper(WooStoreScraper):
    SOURCE = "rbtechngames.com"
    BASE = "https://rbtechngames.com"
    CATEGORIES = [
        ("graphics-card",  "gpu"),
        ("processors",     "cpu"),
        ("motherboards",   "motherboard"),
        ("rams",           "ram"),
        ("storage",        "ssd"),   # covers SSD + HDD
        ("power-supplies", "psu"),
        ("casings",        "case"),
        ("cpu-coolers",    "cooling"),
        ("monitors",       "monitor"),
    ]


class RedTechScraper(WooStoreScraper):
    SOURCE = "redtech.pk"
    BASE = "https://redtech.pk"
    CATEGORIES = [
        ("processor",       "cpu"),
        ("graphic-card",    "gpu"),
        ("motherboard",     "motherboard"),
        ("ram",             "ram"),
        ("ssd-nvme",        "ssd"),
        ("hard-drive",      "hdd"),
        ("power-supply",    "psu"),
        ("gaming-case",     "case"),
        ("cpu-cooler",      "cooling"),
        ("gaming-monitors", "monitor"),
    ]


class TechArcScraper(WooStoreScraper):
    SOURCE = "techarc.pk"
    BASE = "https://techarc.pk"
    THUMB_KEY = "src"       # the listing showed the full-size image
    # HDD intentionally skipped: no dedicated leaf, "storage" duplicates SSD.
    # "cpu-coolers" covers both air and AIO, matching our "cooling".
    CATEGORIES = [
        ("cpu-processors",      "cpu"),
        ("graphics-cards",      "gpu"),
        ("motherboards",        "motherboard"),
        ("ram-memory-modules",  "ram"),
        ("solid-state-drives",  "ssd"),
        ("power-supplies",      "psu"),
        ("cases",               "case"),
        ("cpu-coolers",         "cooling"),
        ("monitors",            "monitor"),
    ]


class TechMatchedScraper(WooStoreScraper):
    SOURCE = "techmatched.pk"
    BASE = "https://techmatched.pk"
    THUMB_KEY = "src"       # the listing showed the full-size image
    CATEGORIES = [
        ("processors",                   "cpu"),
        ("graphics-card-in-pakistan",    "gpu"),
        ("motherboards",                 "motherboard"),
        ("rams",                         "ram"),
        ("find-ssd-prices-in-pakistan",  "ssd"),
        ("hard-drive",                   "hdd"),
        ("buy-power-supply-in-pakistan", "psu"),
        ("computer-case",                "case"),
        ("cpu-coolers",                  "cooling"),
        ("gaming-monitors",              "monitor"),
    ]

    def clean_name(self, name: str) -> str:
        # API names are SEO titles: "Buy <name> in Pakistan | TechMatched".
        name = name.strip()
        if name.lower().startswith("buy "):
            name = name[4:].strip()
        if name.lower().endswith("| techmatched"):
            name = name[: -len("| techmatched")].rstrip(" |").strip()
        return re.sub(r'\s+in\s+pakistan$', '', name, flags=re.IGNORECASE)


# run_all.py source keys
STORES = {
    "amd": AmdHouseScraper,
    "rbt": RbtScraper,
    "redtech": RedTechScraper,
    "tech": TechArcScraper,
    "techmatched": TechMatchedScraper,
}


def main():
    """Standalone smoke test — does NOT write to DB."""
    key = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    if key not in STORES:
        sys.exit(f"usage: python -m scrapers.woo.stores {{{'|'.join(STORES)}}} [--all]")
    cls = STORES[key]
    run_listing_cli(cls, cls.CATEGORIES, lambda slug: slug, write_db=False, default_filter="gpu")


if __name__ == "__main__":
    main()
