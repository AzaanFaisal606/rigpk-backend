"""scripts/refresh_site_cache.py: the request it sends and how it fails.

No network: every call goes through a fake opener.
"""

import io
import json
from email.message import Message
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from scripts import refresh_site_cache as rsc


class _Resp:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(status=200, raises=None, seen=None):
    def opener(req, timeout):
        if seen is not None:
            seen.append(req)
        if raises is not None:
            raise raises
        return _Resp(status)
    return opener


def test_request_targets_production_catalog_tag():
    seen = []
    rsc.refresh("tok", "team_x", opener=_opener(seen=seen))
    (req,) = seen
    url = urllib.parse.urlparse(req.full_url)
    assert url.netloc == "api.vercel.com"
    assert url.path == "/v1/edge-cache/invalidate-by-tags"
    assert urllib.parse.parse_qs(url.query) == {
        "projectIdOrName": ["ppc-frontend"], "teamId": ["team_x"]}
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer tok"
    assert json.loads(req.data) == {"tags": ["catalog"], "target": "production"}


def test_tag_matches_frontend_constant():
    ts = Path(__file__).resolve().parents[1] / "frontend" / "lib" / "catalog-cache.ts"
    if not ts.exists():  # frontend is a separate repo; absent in a backend-only checkout
        pytest.skip("frontend checkout not present")
    assert f'export const CATALOG_TAG = "{rsc.CATALOG_TAG}";' in ts.read_text()


def test_non_200_raises():
    with pytest.raises(RuntimeError, match="HTTP 202"):
        rsc.refresh("tok", "team_x", opener=_opener(status=202))


def test_http_error_carries_reason():
    err = urllib.error.HTTPError("u", 403, "Forbidden", Message(), io.BytesIO(b'{"error":"bad token"}'))
    with pytest.raises(RuntimeError, match="403.*bad token"):
        rsc.refresh("tok", "team_x", opener=_opener(raises=err))


def test_network_error_raises():
    with pytest.raises(RuntimeError, match="could not reach"):
        rsc.refresh("tok", "team_x", opener=_opener(raises=urllib.error.URLError("down")))


def test_main_fails_without_credentials(monkeypatch, capsys):
    monkeypatch.delenv("VERCEL_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_TEAM_ID", raising=False)
    assert rsc.main() == 1
    assert "VERCEL_TOKEN" in capsys.readouterr().out


def test_main_reports_failure(monkeypatch, capsys):
    monkeypatch.setenv("VERCEL_TOKEN", "tok")
    monkeypatch.setenv("VERCEL_TEAM_ID", "team_x")

    def boom(*a, **k):
        raise RuntimeError("Vercel returned HTTP 500")
    monkeypatch.setattr(rsc, "refresh", boom)
    assert rsc.main() == 1
    assert "refresh failed" in capsys.readouterr().out
