"""
Model -> socket, for CPUs whose listing never states a socket.

Only 10.8% of active CPU listings name their socket, which left the /build
compatibility checker firing on roughly one build in ten. Patterns are ordered
most-specific first.

Maintenance: add a row when a new CPU generation launches. A model that is not
matched yields no socket at all — a wrong socket is worse than a missing one,
because the checker would then confidently pass an incompatible pair.
"""
import re

SOCKET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bryzen\s*[3579]\s*9\d{3}", re.I), "AM5"),      # 9000 series
    (re.compile(r"\bryzen\s*[3579]\s*7\d{3}", re.I), "AM5"),      # 7000 series
    (re.compile(r"\bryzen\s*[3579]\s*5\d{3}", re.I), "AM4"),      # 5000 series
    (re.compile(r"\bryzen\s*[3579]\s*3\d{3}", re.I), "AM4"),      # 3000 series
    (re.compile(r"\bcore\s*ultra\s*[579]\b", re.I), "LGA1851"),   # Arrow Lake
    (re.compile(r"\bi[3579][\s-]*1[34]\d{3}", re.I), "LGA1700"),  # 13th/14th gen
    (re.compile(r"\bi[3579][\s-]*12\d{3}", re.I), "LGA1700"),     # 12th gen
    (re.compile(r"\bi[3579][\s-]*1[01]\d{3}", re.I), "LGA1200"),  # 10th/11th gen
]


def socket_for(name: str) -> str | None:
    for pattern, socket in SOCKET_PATTERNS:
        if pattern.search(name):
            return socket
    return None
