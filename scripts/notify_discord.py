"""
Post a scrape summary to a Discord channel via webhook.

Runs after the scrapers in CI. Reads the per-source outcomes straight from the
`scrape_runs` log (written by both orchestrators, failures included) plus the
current DB totals, and posts one embed. The workflow step outcomes are passed in
so a hard crash — one that dies before writing scrape_runs — is still reported
instead of silently showing last run's numbers.

Env:
    DISCORD_WEBHOOK_URL  required; if unset the script warns and exits 0 (a
                         missing webhook must not fail the scrape job).
    TURSO_* / DB_PATH    same env-driven DB selection as everything else.

Usage:
    python scripts/notify_discord.py --parts-status success --prebuilts-status failure
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except Exception:
    pass

from db.database import get_db

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "ppc.db")

GREEN = 0x2ECC71
ORANGE = 0xE67E22
RED = 0xE74C3C


def _fmt_source_lines(health: dict[str, dict]) -> str:
    """
    One line per source: ✅/❌, name, before → after active count, scraped count,
    and a short error on failures.

        ✅ `pakbyte.pk` — 2,589 → 2,594  (scraped 2,594)
        ❌ `techmatched.pk` — 317 → 317  kept — HostBlocked: ...
    """
    if not health:
        return "_no runs recorded_"
    lines = []
    for source in sorted(health):
        h = health[source]
        ok = not h["stale"]
        marker = "✅" if ok else "❌"
        before, after = h.get("before_active"), h.get("after_active")

        if before is None or after is None:
            # Pre-migration row — no baseline captured yet.
            counts = f"{h['last_products']:,} scraped"
        else:
            counts = f"{before:,} → {after:,}"
            counts += f"  (scraped {h['last_products']:,})" if ok else "  kept"

        line = f"{marker} `{source}` — {counts}"
        if not ok and h.get("last_error"):
            err = h["last_error"].replace("\n", " ")
            if len(err) > 70:
                err = err[:67] + "..."
            line += f" — _{err}_"
        lines.append(line)
    return "\n".join(lines)


def build_embed(parts_status: str, prebuilts_status: str) -> dict:
    # Read-only reporting — never migrates the DB it reports on. Without the
    # explicit False this raises against Turso, which is where scrape.yml
    # always runs it.
    with get_db(DB_PATH, allow_remote_migrations=False) as db:
        parts_health = db.source_health(kind="parts")
        prebuilt_health = db.source_health(kind="prebuilt")
        stats = db.stats()
        pre_stats = db.prebuilt_stats()

    # A "failure" means the orchestrator exited non-zero (anomaly / crash); a
    # "cancelled" means the job hit its timeout or hung. A stale source means its
    # latest recorded run didn't earn a sweep. Any of these colours away from green.
    step_failed = any(s in ("failure", "cancelled") for s in (parts_status, prebuilts_status))
    any_stale = any(h["stale"] for h in parts_health.values()) or any(
        h["stale"] for h in prebuilt_health.values()
    )
    if step_failed:
        color, icon, verdict = RED, "❌", "one or more scrapers failed"
    elif any_stale:
        color, icon, verdict = ORANGE, "⚠️", "completed with stale sources"
    else:
        color, icon, verdict = GREEN, "✅", "all sources fresh"

    # stats()['total_parts'] already counts only is_active=1 rows.
    summary = (
        f"**Active parts:** {stats['total_parts']:,}"
        f"  ·  **Prebuilts:** {pre_stats['total']:,}"
        f"  ·  **Price rows:** {stats['total_price_rows']:,}\n"
        f"parts step: `{parts_status}`  ·  prebuilts step: `{prebuilts_status}`"
    )

    return {
        "title": f"{icon} RigPK weekly scrape — {verdict}",
        "description": summary,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": [
            {"name": "Part retailers", "value": _fmt_source_lines(parts_health), "inline": False},
            {"name": "Prebuilt sources", "value": _fmt_source_lines(prebuilt_health), "inline": False},
        ],
        "footer": {"text": "RigPK price bot"},
    }


def post(webhook: str, embed: dict) -> None:
    payload = json.dumps({"embeds": [embed]}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "RigPK-PriceBot/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord returned HTTP {resp.status}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts-status", default="unknown")
    ap.add_argument("--prebuilts-status", default="unknown")
    args = ap.parse_args()

    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("DISCORD_WEBHOOK_URL not set — skipping Discord notification.")
        return 0

    embed = build_embed(args.parts_status, args.prebuilts_status)
    try:
        post(webhook, embed)
        print("Posted scrape summary to Discord.")
    except (urllib.error.URLError, RuntimeError) as e:
        # Notification failure must not fail the job — the scrape already ran.
        print(f"WARNING: failed to post to Discord: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
