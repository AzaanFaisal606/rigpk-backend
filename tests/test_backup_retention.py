"""
backup_db's prune glob is `ppc.db.bak.*`.

It never matched the undated `ppc.db.bak` — a permanent orphan that could never
self-clean — and it DOES match a manually-named `pre-*` snapshot, which was one
automatic backup away from being silently rotated away.
"""
import sqlite3

from db.database import backup_db


def _make_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.commit()
    con.close()


def test_prune_keeps_newest_n(tmp_path):
    src = tmp_path / "ppc.db"
    _make_db(src)
    for _ in range(5):
        backup_db(src, keep=3)
    snaps = sorted(tmp_path.glob("ppc.db.bak.*"))
    assert len(snaps) <= 3


def test_prune_does_not_touch_manual_snapshots(tmp_path):
    src = tmp_path / "ppc.db"
    _make_db(src)
    manual = tmp_path / "ppc.db.bak.pre-migration-keepme"
    manual.write_bytes(b"manual snapshot")
    for _ in range(6):
        backup_db(src, keep=2)
    assert manual.exists(), "a manually-named snapshot must never be rotated away"
