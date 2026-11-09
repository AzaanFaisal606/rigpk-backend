"""Assemble Details {overview, highlights, specs} from per-kind raw retailer data."""
from __future__ import annotations

import hashlib
import json
import re

from scrapers.details.blocks import (
    is_spec_list, list_blocks, paragraphs, split_kv, table_specs,
)
from scrapers.details.clean import _SENTENCE_SPLIT, is_boilerplate, strip_boilerplate, truncate
from scrapers.details.html_tree import find_all, parse_html, text

PARSER_VERSION = 3

OVERVIEW_CAP = 400
OVERVIEW_MIN = 60
HIGHLIGHT_CAP = 300
HIGHLIGHT_MAX = 12
SPEC_MAX = 80

_HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.IGNORECASE | re.DOTALL)


def woo_raw(item: dict, source: str) -> dict:
    return {
        "kind": "woo",
        "source": source,
        "short_description": item.get("short_description") or "",
        "description": item.get("description") or "",
    }


def shopify_raw(item: dict, source: str) -> dict:
    return {"kind": "shopify", "source": source, "body_html": item.get("body_html") or ""}


def webx_raw(item: dict, source: str) -> dict:
    return {
        "kind": "webx",
        "source": source,
        "desc": item.get("productDesc") or "",
        "highlights": item.get("productHighlights") or "",
    }


def raw_hash(raw: dict) -> str:
    blob = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cut_at_seo_heading(html: str) -> str:
    for m in _HEADING_RE.finditer(html):
        if "price in pakistan" in text(parse_html(m.group(2))).lower():
            return html[: m.start()]
    return html


def _html_layers(html: str) -> tuple[list[list], list[str], list[str]]:
    """specs, highlight lists (flattened in order), paragraphs for one HTML string."""
    root = parse_html(html)
    specs = table_specs(root)
    highlights: list[str] = []
    for items in list_blocks(root):
        if is_spec_list(items):
            for item in items:
                kv = split_kv(item)
                if kv:
                    specs.append([None, kv[0], kv[1]])
                else:
                    highlights.append(item)
        else:
            highlights.extend(items)
    return specs, highlights, paragraphs(root)


def _pick_overview(paras: list[str], source: str, name: str) -> str | None:
    for p in paras:
        flags = [is_boilerplate(x, source) for x in _SENTENCE_SPLIT.split(p.strip()) if x]
        # boilerplate followed by kept text is an ad continuation; trailing boilerplate is just trimmed
        if True in flags and False in flags[flags.index(True):]:
            continue
        clean = strip_boilerplate(p, source)
        if len(clean) >= OVERVIEW_MIN and clean.lower() != name.lower():
            return truncate(clean, OVERVIEW_CAP)
    return None


def _finish_highlights(items: list[str], source: str, name: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for h in items:
        h = strip_boilerplate(h, source)
        key = h.lower()
        if not h or key == name.lower() or key in seen:
            continue
        seen.add(key)
        out.append(truncate(h, HIGHLIGHT_CAP))
        if len(out) >= HIGHLIGHT_MAX:
            break
    return out


def _finish_specs(specs: list[list]) -> list[list]:
    out: list[list] = []
    seen: set[tuple] = set()
    for g, label, value in specs:
        key = (g, label.lower(), value.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append([g, label, value])
        if len(out) >= SPEC_MAX:
            break
    return out


def _result(overview, highlights, specs, source, name) -> dict:
    return {
        "overview": overview,
        "highlights": _finish_highlights(highlights, source, name),
        "specs": _finish_specs(specs),
    }


def _parse_html_fields(htmls: list[str], source: str, name: str) -> dict:
    specs: list[list] = []
    highlights: list[str] = []
    overview = None
    for html in htmls:
        s, h, p = _html_layers(html)
        specs += s
        highlights += h
        if overview is None:
            overview = _pick_overview(p, source, name)
    return _result(overview, highlights, specs, source, name)


def _parse_webx(raw: dict, source: str, name: str) -> dict:
    highlights = [h.strip() for h in (raw.get("highlights") or "").split("_") if h.strip()]
    rest: list[str] = []
    for line in (raw.get("desc") or "").splitlines():
        line = line.strip()
        if line.startswith("•"):
            highlights.append(line.lstrip("•").strip())
        elif line:
            rest.append(line)
    overview = strip_boilerplate(" ".join(rest), source)
    if not overview or overview.lower() == name.lower():
        overview = None
    else:
        overview = truncate(overview, OVERVIEW_CAP)
    return _result(overview, highlights, [], source, name)


def _parse_zah(raw: dict, source: str, name: str) -> dict:
    hl_root = parse_html(raw.get("highlights_html") or "")
    highlights = [t for t in (text(li) for li in find_all(hl_root, "li")) if t]
    desc_root = parse_html(raw.get("desc_html") or "")
    paras = paragraphs(desc_root)
    # zah pastes multi-paragraph category templates ("Welcome to the Storage
    # section…", "we also offer thermal paste…") under real products. Shop
    # voice followed by more text anywhere in the tab marks the whole tab as
    # template, so no paragraph of it is taken as this product's overview. A
    # store mention only at the very end is still just trimmed.
    flags = [is_boilerplate(x, source) for p in paras for x in _SENTENCE_SPLIT.split(p.strip()) if x]
    templated = True in flags and False in flags[flags.index(True):]
    overview = None if templated else _pick_overview(paras, source, name)
    return _result(overview, highlights, table_specs(desc_root), source, name)


def parse_details(raw: dict, name: str = "") -> dict:
    kind = raw.get("kind")
    source = raw.get("source", "")
    if kind == "woo":
        return _parse_html_fields(
            [raw.get("short_description") or "", raw.get("description") or ""], source, name
        )
    if kind == "shopify":
        return _parse_html_fields([_cut_at_seo_heading(raw.get("body_html") or "")], source, name)
    if kind == "webx":
        return _parse_webx(raw, source, name)
    if kind == "zah_page":
        return _parse_zah(raw, source, name)
    raise ValueError(f"unknown details kind: {kind!r}")
