"""Sentence-level boilerplate stripping and truncation for description text."""
from __future__ import annotations

import re

STORE_NAMES: dict[str, list[str]] = {
    "amdhouse.pk": ["amd house"],
    "rbtechngames.com": ["rb tech", "rbtech"],
    "redtech.pk": ["redtech", "red tech"],
    "techarc.pk": ["tech arc", "techarc"],
    "techmatched.pk": ["techmatched", "tech matched"],
    "pakbyte.pk": ["pakbyte"],
    "czone.com.pk": ["czone", "computer zone"],
    "junaidtech.pk": ["junaid"],
    "zahcomputers.pk": ["zah computers", "zahcomputers", "zah"],
}

_GENERIC = re.compile(
    # Any sentence naming the country is retailer copy ("Buy X in Pakistan",
    # "Free Delivery All Over Pakistan", "Fulfilled by Xigmatek Pakistan").
    r"\bpakistan\b|price[- ]match|available at|best price|check availability"
    r"|match the price|manufacturer'?s website|read (?:here|details)"
    r"|free delivery|fulfilled by"
    # Shop voice from the category templates zah pastes onto every product
    # ("we also offer high-quality thermal paste", "we have the perfect GPU
    # to match your needs"). Not "welcome to": AMD's own copy opens with
    # "Welcome to the new era of performance".
    r"|\bwe (?:also )?(?:offer|have|stock|carry|provide)\b"
    r"|\bour (?:store|shop|range|collection|selection|website)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def is_boilerplate(sentence: str, source: str) -> bool:
    if _GENERIC.search(sentence):
        return True
    for name in STORE_NAMES.get(source, []):
        if re.search(rf"\b{re.escape(name)}\b", sentence, re.IGNORECASE):
            return True
    return False


def strip_boilerplate(text: str, source: str) -> str:
    kept = [s for s in _SENTENCE_SPLIT.split(text.strip()) if s and not is_boilerplate(s, source)]
    return " ".join(kept)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", text) if m.end() <= limit]
    if ends:
        return text[: ends[-1]].rstrip()
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip() + "…"
