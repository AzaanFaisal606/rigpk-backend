"""
zahcomputers.pk product-page details.

The zah listing carries no descriptions, so each part needs one product-page
request. Pages are located by stable anchors (the "Key Highlights" label and
the tab-pane-desc / tab-pane-specs ids), never by the hashed CSS-module classes.
"""
from __future__ import annotations

import re
import time
import urllib.error

from scrapers.zah.html.scraper import SOURCE, ZahHtmlScraper

ZAH_DETAILS_PER_RUN = 300
FLUSH_EVERY = 25
MAX_CONSECUTIVE_FAILURES = 3
SKIP_CODES = (404, 410)

_LABEL_RE = re.compile(r">\s*Key Highlights\s*<", re.IGNORECASE)
_LIST_TAG_RE = re.compile(r"<(/?)ul\b[^>]*>", re.IGNORECASE)
_DESC_ID = 'id="tab-pane-desc"'
_SPECS_ID = 'id="tab-pane-specs"'


def _highlights_html(page: str) -> str:
    label = _LABEL_RE.search(page)
    if not label:
        return ""
    start = None
    depth = 0
    for m in _LIST_TAG_RE.finditer(page, label.end()):
        if m.group(1):
            depth -= 1
            if depth == 0 and start is not None:
                return page[start:m.end()]
        else:
            if start is None:
                start = m.start()
            depth += 1
    return page[start:] if start is not None else ""


def _desc_html(page: str) -> str:
    i = page.find(_DESC_ID)
    if i < 0:
        return ""
    begin = page.find(">", i)
    if begin < 0:
        return ""
    begin += 1
    j = page.find(_SPECS_ID, begin)
    if j < 0:
        return ""
    end = page.rfind("<", begin, j)       # opening tag of the specs pane
    if end < 0:
        return ""
    block = page[begin:end]
    # drop the trailing "<!-- Tab 2 ... -->" comment that precedes the specs pane
    k = block.rfind("<!--")
    if k >= 0 and "-->" in block[k:] and not block[k:].split("-->", 1)[1].strip():
        block = block[:k]
    return block.strip()


def extract_zah_raw(page_html: str) -> dict:
    return {
        "kind": "zah_page",
        "source": SOURCE,
        "highlights_html": _highlights_html(page_html),
        "desc_html": _desc_html(page_html),
    }


def _http_code(exc: BaseException) -> int | None:
    """fetch() wraps HTTP errors in RuntimeError(... ) from HTTPError."""
    for e in (exc, exc.__cause__):
        if isinstance(e, urllib.error.HTTPError):
            return e.code
    return None


def fetch_zah_details(db, limit: int | None, scraper=None, sleep=time.sleep) -> dict:
    """Fetch product pages for active zah parts with no details and store them."""
    scraper = scraper or ZahHtmlScraper()
    parts = db.parts_missing_details(SOURCE, limit or 10**9)
    fetched = written = skipped = 0
    failures = 0
    stopped = False
    batch: list[tuple[int, dict, str]] = []

    def flush():
        nonlocal written
        if batch:
            written += db.write_details(batch)
            db._conn.commit()
            batch.clear()

    for part in parts:
        try:
            page = scraper.fetch(part["url"])
        except Exception as e:
            if _http_code(e) in SKIP_CODES:
                skipped += 1
                failures = 0
                continue
            failures += 1
            print(f"  zah details: fetch failed ({failures}/{MAX_CONSECUTIVE_FAILURES}): {part['url']}: {e}")
            if failures >= MAX_CONSECUTIVE_FAILURES:
                stopped = True
                break
            continue
        failures = 0
        fetched += 1
        batch.append((part["id"], extract_zah_raw(page), part["name"]))
        if fetched % FLUSH_EVERY == 0:
            flush()
            print(f"  zah details: {fetched}/{len(parts)} fetched, {written} written")
    flush()
    return {"fetched": fetched, "written": written, "skipped": skipped, "stopped": stopped}
