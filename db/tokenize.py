"""
The single definition of how RigPK splits a product name or a search query
into tokens.

Both sides of search depend on this agreeing exactly:
  - `normalize_name()` builds the `parts.name_norm` column at upsert time.
  - `search_tokens()` splits what the user typed.

`frontend/lib/search-tokenize.ts` is the TypeScript twin. Both are asserted
against `tests/fixtures/tokenizer_cases.json`, so a divergence fails CI on one
side or the other.
"""
from __future__ import annotations

import re

# Cap on tokens per *query*. Each token costs one more LIKE condition, so an
# unbounded count is a cheap way to make the DB do work. Deliberately NOT
# applied to normalize_name(): a long product name must normalise in full or
# its tail becomes unsearchable.
MAX_SEARCH_TOKENS = 8

# Split on any run of non-alphanumerics, and at letter<->digit boundaries
# where the alphabetic side is two or more letters. That second rule is what
# makes "5060ti" match "RTX 5060 Ti" and "5090" match a catalogue entry
# written "RTX5090".
#
# The two-letter minimum is load-bearing, not cosmetic. Splitting at EVERY
# letter<->digit boundary turns "i5" into ["i", "5"], which matches any name
# containing a lone "i" and a lone "5" — 1109 of 7931 active parts, including
# Core i7 and i9 CPUs. With the minimum it matches 50, with no false hits,
# and model numbers like "9800x3d" and "x670e" survive intact.
_SPLIT_RE = re.compile(
    r"[^0-9a-z]+"
    r"|(?<=[0-9])(?=[a-z]{2})"      # digits -> 2+ letters:  5060|ti
    r"|(?<=[a-z]{2})(?=[0-9])"      # 2+ letters -> digits:  rtx|5090
)


def _split(text: str) -> list[str]:
    return [t for t in _SPLIT_RE.split(text.lower()) if t]


def search_tokens(q: str) -> list[str]:
    """Split a user query into deduplicated, order-preserving, capped tokens."""
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in _split(q):
        if raw in seen:
            continue
        seen.add(raw)
        tokens.append(raw)
        if len(tokens) >= MAX_SEARCH_TOKENS:
            break
    return tokens


def normalize_name(name: str) -> str:
    """
    Build the stored match target for a product name.

    Space-padded on both sides so a `LIKE '% token%'` parameter anchors at a
    word start — that is what stops the token "ti" matching the "ti" inside
    "Edition". Not deduplicated and not capped: this is an index, not a query.
    """
    return " " + " ".join(_split(name)) + " " if _split(name) else " "
