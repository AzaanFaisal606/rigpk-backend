"""
Neutralise instruction-shaped text in a scrape log before it is handed to the
self-heal action.

The log legitimately contains retailer-controlled strings (product names), and
the action that reads it holds repo write. This does not attempt to be a
complete prompt-injection defence — it removes the cheap vectors and caps line
length so a single crafted product name cannot dominate the prompt.

Usage: python -m scripts.sanitize_log < raw.log > safe.log
"""
import re
import sys

_MAX_LINE = 1000

_PATTERNS = [
    re.compile(r"<!--.*?-->", re.DOTALL),                     # HTML comments
    re.compile(r"<[^>]{0,200}>"),                             # any tag-shaped run
    re.compile(r"(?i)ignore\s+(all\s+)?previous\s+instructions?"),
    re.compile(r"(?i)disregard\s+(all\s+)?(prior|previous|above)"),
    re.compile(r"(?i)you\s+are\s+now\s+"),
    re.compile(r"(?i)new\s+instructions?\s*:"),
    re.compile(r"(?i)system\s*prompt"),
]


def sanitize(text: str) -> str:
    lines = []
    for line in text.splitlines():
        for pat in _PATTERNS:
            line = pat.sub("[redacted]", line)
        if len(line) > _MAX_LINE:
            suffix = " …[truncated]"
            line = line[:_MAX_LINE - len(suffix)] + suffix
        lines.append(line)
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.write(sanitize(sys.stdin.read()))
