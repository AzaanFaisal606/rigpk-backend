#!/usr/bin/env python3
"""
Mark the site's cached catalogue pages stale after a scrape writes the DB.

The frontend caches every page built from scraped data (home, trends, part,
model and budget pages, the sitemap) under one cache tag and keeps it until
told otherwise: a page rebuild is an ISR write, and Vercel Hobby allows 200k
write units a month. This script tells Vercel the data changed. Each tagged
page then rebuilds once, on its next visit, and keeps serving the old copy
until that rebuild is done (stale-while-revalidate, not a purge).

It calls Vercel's REST API rather than a route on the site, because the site's
firewall challenges non-browser clients.

Run it after any scrape that wrote production data (scrape.yml and rerun.yml do
this themselves). After a local run, e.g. `python run_all.py zah`:

    python scripts/refresh_site_cache.py

Env: VERCEL_TOKEN (secret) and VERCEL_TEAM_ID, from .env or GitHub secrets.
VERCEL_PROJECT defaults to ppc-frontend. Exits 1 when the call fails, so the
workflow step can report it.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except Exception:
    pass

# Must match CATALOG_TAG in frontend/lib/catalog-cache.ts.
CATALOG_TAG = "catalog"
DEFAULT_PROJECT = "ppc-frontend"
API_URL = "https://api.vercel.com/v1/edge-cache/invalidate-by-tags"


def build_request(token: str, team_id: str, project: str,
                  tags: list[str]) -> urllib.request.Request:
    qs = urllib.parse.urlencode({"projectIdOrName": project, "teamId": team_id})
    body = json.dumps({"tags": tags, "target": "production"}).encode()
    return urllib.request.Request(
        f"{API_URL}?{qs}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )


def refresh(token: str, team_id: str, project: str = DEFAULT_PROJECT,
            tags: list[str] | None = None, opener=urllib.request.urlopen) -> None:
    """Invalidate the tags; raises RuntimeError on any failure."""
    req = build_request(token, team_id, project, tags or [CATALOG_TAG])
    try:
        with opener(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Vercel returned HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        # The body says why (bad token, wrong team); it carries no secrets.
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Vercel returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach Vercel: {e.reason}") from e


def main() -> int:
    token = os.getenv("VERCEL_TOKEN", "").strip()
    team_id = os.getenv("VERCEL_TEAM_ID", "").strip()
    project = os.getenv("VERCEL_PROJECT", "").strip() or DEFAULT_PROJECT
    if not token or not team_id:
        print("ERROR: VERCEL_TOKEN and VERCEL_TEAM_ID must be set; "
              "the site keeps serving its old pages until this runs.")
        return 1
    try:
        refresh(token, team_id, project)
    except RuntimeError as e:
        print(f"ERROR: site cache refresh failed: {e}")
        return 1
    print(f"Marked cache tag '{CATALOG_TAG}' stale on {project}; "
          "pages rebuild on their next visit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
