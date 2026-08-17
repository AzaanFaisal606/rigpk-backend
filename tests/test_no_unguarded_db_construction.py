"""
Static guard against the defect class that took the API down.

Every `Database(...)` / `get_db(...)` call site in production code must say
what it does about a remote schema — `allow_remote_migrations=True` if it
owns the schema, `False` if it only reads and writes rows. A call site that
says nothing resolves to "refuse" and raises the moment TURSO_DATABASE_URL
is set, which is every deployed run and every local run picking up this
repo's `.env`. That is not a failure a unit test would catch, because the
whole suite runs against local SQLite where the guard never fires — so it
is checked here by reading the source instead.

Migration scripts are exempt when they call `resolve_target()`, which sets
ALLOW_REMOTE_MIGRATIONS process-wide only after confirming the target.
Tests are exempt: conftest.py strips TURSO_* so they are always local.
"""
import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TARGETS = ("Database", "get_db")

# db/database.py defines both; it is the thing being guarded, not a caller.
_EXEMPT_FILES = {_ROOT / "db" / "database.py"}

_SKIP_DIRS = {"tests", ".venv", "venv", "node_modules", ".git", "frontend", "data",
              "docs", ".superpowers", "backups"}


def _production_sources() -> list[Path]:
    out = []
    for p in sorted(_ROOT.rglob("*.py")):
        rel = p.relative_to(_ROOT)
        if rel.parts and rel.parts[0] in _SKIP_DIRS:
            continue
        if p in _EXEMPT_FILES:
            continue
        out.append(p)
    return out


def _unguarded_calls(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    # A module that calls resolve_target() anywhere has opted in via the
    # migration guard, which sets ALLOW_REMOTE_MIGRATIONS for the process.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "resolve_target"
        ):
            return []

    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in _TARGETS:
            continue
        if any(kw.arg == "allow_remote_migrations" for kw in node.keywords):
            continue
        bad.append((node.lineno, name))
    return bad


@pytest.mark.parametrize(
    "path", _production_sources(), ids=lambda p: str(p.relative_to(_ROOT))
)
def test_every_db_construction_declares_its_remote_stance(path: Path):
    bad = _unguarded_calls(path)
    assert not bad, (
        f"{path.relative_to(_ROOT)} constructs the database without saying "
        "whether it may migrate a remote target: "
        + ", ".join(f"line {ln}: {nm}()" for ln, nm in bad)
        + ". Pass allow_remote_migrations=True if this caller owns the "
        "schema, False if it only reads/writes rows, or call "
        "resolve_target() first if it is a migration script."
    )


def test_the_scan_actually_finds_something():
    """A scanner that silently matches nothing would pass forever."""
    assert len(_production_sources()) > 10
