"""Block extractors (tables, lists, paragraphs) over the tolerant HTML tree."""
from __future__ import annotations

import re

from scrapers.details.html_tree import BLOCK, Node, text

Spec = list  # [group: str | None, label: str, value: str]

DROP_LABELS = {"sku", "ean", "upc", "mpn", "price", "payment methods", "note", "notes"}
VALUE_CAP = 400
_HEADER_WORDS = re.compile(
    r"^(specifications?( group)?|details?|features?|value|description)$", re.IGNORECASE
)
_KV = re.compile(r"^([^:]{1,30}):\s*(.{1,60})$")
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _kids(node: Node, *tags: str) -> list[Node]:
    return [c for c in node.children if isinstance(c, Node) and c.tag in tags]


def _clean_label(s: str) -> str:
    return s.strip().rstrip(":").strip()


def _clean_value(s: str) -> str:
    s = re.sub(r"^(?:\s*/\s*)+|(?:\s*/\s*)+$", "", s).strip()
    return s[:VALUE_CAP].rstrip()


def _is_header(cells: list[Node], in_thead: bool) -> bool:
    texts = [text(c) for c in cells]
    if all(_HEADER_WORDS.match(t) for t in texts):
        return True
    return in_thead


def _segments(cell: Node) -> list[tuple[str, list]]:
    """Split a value cell on <strong>Label:</strong> markers."""
    segs: list[tuple[str, list]] = []
    kids = cell.children
    i = 0
    while i < len(kids):
        k = kids[i]
        if isinstance(k, Node) and k.tag == "strong":
            label = text(k)
            nxt = kids[i + 1] if i + 1 < len(kids) else None
            colon_after = isinstance(nxt, str) and nxt.lstrip().startswith(":")
            if label.endswith(":") or colon_after:
                body: list = []
                i += 1
                if colon_after:
                    body.append(nxt.lstrip()[1:])
                    i += 1
                while i < len(kids) and not (isinstance(kids[i], Node) and kids[i].tag == "strong"):
                    body.append(kids[i])
                    i += 1
                segs.append((_clean_label(label), body))
                continue
        i += 1
    return segs


def _add(out: list[Spec], group: str | None, label: str, value: str) -> None:
    label = _clean_label(label)
    value = _clean_value(value)
    if not label or not value or label.lower() in DROP_LABELS:
        return
    out.append([group, label, value])


def _row(cells: list[Node], in_thead: bool, out: list[Spec]) -> None:
    if len(cells) != 2 or _is_header(cells, in_thead):
        return
    left, right = cells
    group_label = text(left)
    strongs = [c for c in right.children if isinstance(c, Node) and c.tag == "strong"]
    if len(strongs) >= 2:
        segs = _segments(right)
        if len(segs) >= 2:
            group = group_label or None
            for label, body in segs:
                holder = Node("td", children=body)
                _add(out, group, label, text(holder, br=" / "))
            return
    _add(out, None, group_label, text(right, br=" / "))


def _walk(node: Node, in_thead: bool, out: list[Spec]) -> None:
    for c in node.children:
        if not isinstance(c, Node):
            continue
        if c.tag == "tr":
            _row(_kids(c, "th", "td"), in_thead, out)
        elif c.tag == "dl":
            pending: str | None = None
            for d in _kids(c, "dt", "dd"):
                if d.tag == "dt":
                    pending = text(d)
                elif pending is not None:
                    _add(out, None, pending, text(d, br=" / "))
                    pending = None
        else:
            _walk(c, in_thead or c.tag == "thead", out)


def table_specs(root: Node) -> list[Spec]:
    out: list[Spec] = []
    _walk(root, False, out)
    return out


def list_blocks(root: Node) -> list[list[str]]:
    blocks: list[list[str]] = []

    def walk(node: Node) -> None:
        for c in node.children:
            if not isinstance(c, Node):
                continue
            if c.tag in ("ul", "ol"):
                items = [text(li) for li in _kids(c, "li")]
                blocks.append([i for i in items if i])
            else:
                walk(c)

    walk(root)
    return blocks


def split_kv(item: str) -> tuple[str, str] | None:
    m = _KV.match(item)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def is_spec_list(items: list[str]) -> bool:
    if not items:
        return False
    hits = sum(1 for i in items if _KV.match(i))
    return hits / len(items) >= 0.6


def _has_block(node: Node) -> bool:
    return any(
        isinstance(c, Node) and (c.tag in BLOCK or _has_block(c)) for c in node.children
    )


def paragraphs(root: Node) -> list[str]:
    out: list[str] = []

    def walk(node: Node) -> None:
        for c in node.children:
            if not isinstance(c, Node):
                continue
            if c.tag in ("table", "ul", "ol") or c.tag in _HEADINGS:
                continue
            if c.tag == "p" or (c.tag == "div" and not _has_block(c)):
                t = text(c)
                if t:
                    out.append(t)
            else:
                walk(c)

    walk(root)
    return out
