"""
Regression coverage for the executescript() -> statement-by-statement
_apply_schema() fix.

Root cause this guards: `self._conn.executescript(open(schema.sql).read())`
silently created NOTHING on libSQL. schema.sql's first statement is
`PRAGMA journal_mode = WAL;`, libsql rejects PRAGMA with a hard
SQL_PARSE_ERROR, and executescript() swallowed that failure and abandoned
every one of the ~19 statements after it — with no exception raised. Every
table/index this repo has ever added to schema.sql silently failed to exist
on Turso.

No test here touches a remote database — everything runs against a local
tmp-file sqlite3 DB, same as the rest of the suite (see conftest.py).
"""
import re
import sqlite3

import pytest

from db.database import Database, _SCHEMA, _split_sql_statements, _strip_sql_line_comments


# ---------------------------------------------------------------------------
# 1. Every table/index schema.sql declares must actually exist after
#    _apply_schema() runs. This is the test that would have caught the bug:
#    it fails if any statement silently gets skipped.
# ---------------------------------------------------------------------------

def _expected_object_names():
    # Comments must be stripped before scanning for names — schema.sql's own
    # prose comments mention "CREATE TABLE IF NOT EXISTS" by name (e.g. "this
    # file's CREATE TABLE IF NOT EXISTS never re-adds columns..."), and a
    # naive regex over the raw text picks up the next comment word as a fake
    # table name.
    text = _strip_sql_line_comments(_SCHEMA.read_text(encoding="utf-8"))
    tables = re.findall(
        r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(", text, re.IGNORECASE
    )
    indexes = re.findall(
        r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS\s+(\w+)\s+ON", text, re.IGNORECASE
    )
    assert tables, "regex found no CREATE TABLE statements — schema.sql format changed?"
    assert indexes, "regex found no CREATE INDEX statements — schema.sql format changed?"
    return tables, indexes


def test_apply_schema_creates_every_declared_table_and_index(tmp_path):
    expected_tables, expected_indexes = _expected_object_names()

    db = Database(tmp_path / "t.db")
    try:
        present_tables = {
            r["name"]
            for r in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        present_indexes = {
            r["name"]
            for r in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    finally:
        db.close()

    missing_tables = set(expected_tables) - present_tables
    missing_indexes = set(expected_indexes) - present_indexes
    assert not missing_tables, f"tables declared in schema.sql but not created: {missing_tables}"
    assert not missing_indexes, f"indexes declared in schema.sql but not created: {missing_indexes}"


# ---------------------------------------------------------------------------
# 2. A malformed statement must raise, not be swallowed.
# ---------------------------------------------------------------------------

def test_malformed_schema_statement_raises(tmp_path, monkeypatch):
    bad_schema = tmp_path / "bad_schema.sql"
    bad_schema.write_text(
        "CREATE TABLE ok_table (id INTEGER);\n"
        "THIS IS NOT VALID SQL AT ALL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("db.database._SCHEMA", bad_schema)

    with pytest.raises(RuntimeError) as exc:
        Database(tmp_path / "t.db")

    msg = str(exc.value)
    assert "THIS IS NOT VALID SQL" in msg, (
        "error message should include the offending statement's opening text"
    )


def test_split_sql_statements_feeds_execute_and_raises_on_bad_one():
    """Same failure mode, exercised directly at the splitter/execute seam."""
    script = "CREATE TABLE ok (id INTEGER); GARBAGE NOT SQL;"
    stmts = _split_sql_statements(script)
    conn = sqlite3.connect(":memory:")
    conn.execute(stmts[0])  # first statement is fine
    with pytest.raises(sqlite3.OperationalError):
        conn.execute(stmts[1])  # second is not — must surface, not vanish


# ---------------------------------------------------------------------------
# 3. Splitter unit tests: comment-aware, not a naive split(";").
# ---------------------------------------------------------------------------

_FIXTURE_SCRIPT = """
-- a line comment holding a semicolon; right there, and an apostrophe: it's fine
CREATE TABLE IF NOT EXISTS widgets (
    id      INTEGER PRIMARY KEY,          -- trailing column comment
    name    TEXT NOT NULL,                -- another one; with a semicolon too
    tag     TEXT DEFAULT 'default'        -- comment with 'quoted; text'
);

CREATE INDEX IF NOT EXISTS idx_widgets_name ON widgets(name);
"""


def test_splitter_produces_exactly_the_real_statements():
    stmts = _split_sql_statements(_FIXTURE_SCRIPT)
    assert len(stmts) == 2, f"expected 2 statements, got {len(stmts)}: {stmts}"


def test_splitter_strips_comments_out_of_statement_text():
    stmts = _split_sql_statements(_FIXTURE_SCRIPT)
    create_table_stmt = stmts[0]
    assert "trailing column comment" not in create_table_stmt
    assert "another one" not in create_table_stmt
    assert "-- " not in create_table_stmt


def test_splitter_does_not_break_on_comment_semicolons():
    """
    The line-comment semicolon and the trailing-comment semicolons must not
    produce extra (empty or truncated) statements.
    """
    stmts = _split_sql_statements(_FIXTURE_SCRIPT)
    assert all(s.strip() for s in stmts), "no blank/whitespace-only statements"
    assert stmts[0].upper().startswith("CREATE TABLE IF NOT EXISTS WIDGETS".split()[0])


def test_splitter_output_is_valid_executable_sql():
    """The multi-line CREATE TABLE must survive as one coherent statement."""
    stmts = _split_sql_statements(_FIXTURE_SCRIPT)
    conn = sqlite3.connect(":memory:")
    for stmt in stmts:
        conn.execute(stmt)
    names = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        ).fetchall()
    }
    assert "widgets" in names
    assert "idx_widgets_name" in names


def test_splitter_drops_comment_only_and_blank_segments():
    script = "-- just a comment\n\n;\nCREATE TABLE t (id INTEGER);\n-- trailing comment only\n"
    stmts = _split_sql_statements(script)
    assert stmts == ["CREATE TABLE t (id INTEGER)"]
