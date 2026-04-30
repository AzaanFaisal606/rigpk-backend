"""
One-time migration: adds `specs` TEXT column to the parts table.
Run from project root: python -m db.migrate_add_specs
Safe to re-run.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "ppc.db"


def run():
    conn = sqlite3.connect(str(DB_PATH))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(parts)")]
    if "specs" not in cols:
        conn.execute("ALTER TABLE parts ADD COLUMN specs TEXT DEFAULT NULL")
        conn.commit()
        print("Migration complete: specs column added.")
    else:
        print("Column already exists — skipping.")
    conn.close()


if __name__ == "__main__":
    run()
