"""
Model -> socket, for CPUs whose listing never states a socket.

Only 10.8% of active CPU listings name their socket, which left the /build
compatibility checker firing on roughly one build in ten. Patterns are ordered
most-specific first.

Every pattern requires the trailing suffix (if any) to be an explicitly
recognized DESKTOP suffix (X, X3D, G, GE, K, KF, KS, F, T). Mobile/soldered
suffixes (H, HS, HX, U, P, V, Y...) are never enumerated, so they fall through
to no match by construction — AMD and Intel both reuse the same digit run
across desktop and mobile parts within a generation (Ryzen 9 5900X desktop vs
5900HX mobile; Core Ultra 7 265K desktop vs 255H mobile), so the suffix is the
only reliable signal. `socket_for()` also bails out entirely on an explicit
laptop/mobile/notebook/BGA marker anywhere in the name, as a second net.

Maintenance: add a row when a new CPU generation launches. A model that is not
matched yields no socket at all — a wrong socket is worse than a missing one,
because the checker would then confidently pass an incompatible pair.
"""
import re

_MOBILE_MARKER_RE = re.compile(r'\b(laptop|mobile|notebook|bga)\b', re.I)

# Desktop-only suffixes. Longer alternatives first so e.g. "KF" isn't
# short-circuited by "K" matching a prefix and leaving a dangling "F".
# "F" is AMD's no-iGPU desktop suffix (e.g. Ryzen 5 7500F/9500F) — the same
# concept as Intel's F, and desktop-only for AMD too (never used on mobile
# parts), so it belongs in the same allow-list.
_AMD_SUFFIX = r'(?:X3D|GE|X|G|F)?'
_INTEL_SUFFIX = r'(?:KF|KS|K|F|T)?'

SOCKET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"\bryzen\s*[3579]\s*9\d{{3}}{_AMD_SUFFIX}\b", re.I), "AM5"),      # 9000 series
    (re.compile(rf"\bryzen\s*[3579]\s*7\d{{3}}{_AMD_SUFFIX}\b", re.I), "AM5"),      # 7000 series
    (re.compile(rf"\bryzen\s*[3579]\s*5\d{{3}}{_AMD_SUFFIX}\b", re.I), "AM4"),      # 5000 series
    (re.compile(rf"\bryzen\s*[3579]\s*3\d{{3}}{_AMD_SUFFIX}\b", re.I), "AM4"),      # 3000 series
    # Arrow Lake-S desktop only (200 series, e.g. 245K/265K/285K). Arrow
    # Lake-H mobile reuses the same number space (e.g. 255H) but always
    # carries a mobile suffix (H/HX/U/V), which _INTEL_SUFFIX excludes.
    # Tier and model number may have up to 2 words between them — some
    # listings write "Core Ultra 5 Desktop Processor 245K" (marketing copy
    # inserted mid-name). Bounded, not open-ended: a DB scan of every row
    # in every category that contains "core ultra" found the loose
    # "core ultra [579]" trigger matches CPU rows only, never a
    # motherboard/cooler name — so this can't pick up an embedded CPU
    # model from a compatibility blurb on a non-CPU listing.
    (re.compile(rf"\bcore\s*ultra\s*[579]\b(?:\s+\w+){{0,2}}\s+2\d{{2}}{_INTEL_SUFFIX}\b", re.I), "LGA1851"),
    (re.compile(rf"\bi[3579][\s-]*1[34]\d{{3}}{_INTEL_SUFFIX}\b", re.I), "LGA1700"),  # 13th/14th gen
    (re.compile(rf"\bi[3579][\s-]*12\d{{3}}{_INTEL_SUFFIX}\b", re.I), "LGA1700"),     # 12th gen
    (re.compile(rf"\bi[3579][\s-]*1[01]\d{{3}}{_INTEL_SUFFIX}\b", re.I), "LGA1200"),  # 10th/11th gen
    # Pentium Gold G6400/G6500/G6600 — 10th gen Comet Lake, unambiguously
    # LGA1200. Deliberately NOT a broad "G6\d{3}" range: Intel reused the G6
    # prefix for 12th-gen Alder Lake Pentium Gold (G6405/G6405T), which is
    # LGA1700 — a wrong-socket regression if matched here. Only the three
    # exact Comet Lake model numbers are listed.
    (re.compile(r"\bpentium\s*gold\s*g6[456]00\b", re.I), "LGA1200"),
]


def socket_for(name: str) -> str | None:
    if _MOBILE_MARKER_RE.search(name):
        return None
    for pattern, socket in SOCKET_PATTERNS:
        if pattern.search(name):
            return socket
    return None
