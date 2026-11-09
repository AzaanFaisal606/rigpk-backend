"""Tolerant HTML tree built on the stdlib parser (retailer markup is sloppy)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}
SKIP = {"script", "style", "iframe", "noscript", "svg"}
BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}

# tag -> (tags that an open one of closes, boundaries that stop the search)
_IMPLICIT = {
    "li": ({"li"}, {"ul", "ol"}),
    "p": ({"p"}, {"td", "th", "li", "div", "table", "ul", "ol"}),
    "tr": ({"tr"}, {"table"}),
    "td": ({"td", "th"}, {"tr", "table"}),
    "th": ({"td", "th"}, {"tr", "table"}),
}


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]
        self.skip_tag: str | None = None
        self.skip_depth = 0

    def _implicit_close(self, tag: str) -> None:
        closes, bounds = _IMPLICIT.get(tag, (set(), set()))
        for i in range(len(self.stack) - 1, 0, -1):
            t = self.stack[i].tag
            if t in closes:
                del self.stack[i:]
                return
            if t in bounds:
                return

    def handle_starttag(self, tag, attrs):
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth += 1
            return
        if tag in SKIP:
            self.skip_tag, self.skip_depth = tag, 1
            return
        self._implicit_close(tag)
        node = Node(tag, {k: (v or "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if self.skip_tag or tag in SKIP:
            return
        self.handle_starttag(tag, attrs)
        if tag not in VOID and len(self.stack) > 1 and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth -= 1
                if self.skip_depth == 0:
                    self.skip_tag = None
            return
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.skip_tag:
            self.stack[-1].children.append(data)


def parse_html(s: str | None) -> Node:
    b = _Builder()
    if s:
        b.feed(s)
        b.close()
    return b.root


def find_all(node: Node, *tags: str) -> list[Node]:
    out: list[Node] = []
    for c in node.children:
        if isinstance(c, Node):
            if c.tag in tags:
                out.append(c)
            out.extend(find_all(c, *tags))
    return out


def _collect(node: "Node | str", br: str, parts: list[str]) -> None:
    if isinstance(node, str):
        parts.append(node)
        return
    if node.tag == "img":
        return
    if node.tag == "br":
        parts.append(br)
        return
    block = node.tag in BLOCK
    if block:
        parts.append(" ")
    for c in node.children:
        _collect(c, br, parts)
    if block:
        parts.append(" ")


def text(node: "Node | str", br: str = " ") -> str:
    parts: list[str] = []
    _collect(node, br, parts)
    return re.sub(r"\s+", " ", "".join(parts).replace("\xa0", " ")).strip()
