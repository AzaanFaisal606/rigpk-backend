"""
render.yaml must declare the DB env vars.

Without them a render.yaml-driven deploy silently falls through to the local
SQLite branch in backend/config.py, creates an empty data/ppc.db, and serves
HTTP 200 with an empty catalogue — a failure that looks exactly like success.
sync: false means "declare the name, prompt for the value in the dashboard",
so no secret enters git.
"""
from pathlib import Path

import yaml

RENDER_YAML = Path(__file__).resolve().parent.parent / "render.yaml"


def _env_vars() -> dict:
    spec = yaml.safe_load(RENDER_YAML.read_text(encoding="utf-8"))
    web = [s for s in spec["services"] if s["type"] == "web"][0]
    return {e["key"]: e for e in web["envVars"]}


def test_render_yaml_declares_turso_vars():
    env = _env_vars()
    assert "TURSO_DATABASE_URL" in env
    assert "TURSO_AUTH_TOKEN" in env


def test_turso_secrets_are_not_committed():
    env = _env_vars()
    for key in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        assert env[key].get("sync") is False, f"{key} must use sync: false"
        assert "value" not in env[key], f"{key} must not carry a value in git"
