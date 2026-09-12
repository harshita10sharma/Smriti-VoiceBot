"""tools/backup_db.py: consistent SQLite backup/restore/verify, exercised
against throwaway databases only -- never the real runtime file."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.backup_db import backup, restore, verify


def _make_db(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    connection.execute('PRAGMA user_version = 6')
    connection.execute('CREATE TABLE users (user_id TEXT PRIMARY KEY, display_name TEXT)')
    connection.execute("INSERT INTO users VALUES ('demo-user', 'Demo')")
    connection.commit()
    connection.close()


def test_backup_produces_a_verified_consistent_copy(tmp_path):
    db_path = tmp_path / 'source.db'
    _make_db(db_path)

    dest = backup(db_path, tmp_path / 'backups')

    assert dest.exists()
    connection = sqlite3.connect(str(dest))
    row = connection.execute('SELECT display_name FROM users WHERE user_id = ?',
                             ('demo-user',)).fetchone()
    connection.close()
    assert row == ('Demo',)


def test_restore_writes_back_and_preserves_the_prior_file(tmp_path):
    source = tmp_path / 'source.db'
    _make_db(source)
    backup_path = backup(source, tmp_path / 'backups')

    target = tmp_path / 'restored.db'
    _make_db(target)  # an existing "current" database to be overwritten
    connection = sqlite3.connect(str(target))
    connection.execute("UPDATE users SET display_name = 'Different'")
    connection.commit()
    connection.close()

    restore(backup_path, target)

    connection = sqlite3.connect(str(target))
    row = connection.execute('SELECT display_name FROM users WHERE user_id = ?',
                             ('demo-user',)).fetchone()
    connection.close()
    assert row == ('Demo',)  # restored content, not the pre-restore edit

    # The pre-restore state was preserved, not silently discarded.
    safety_copy = target.with_suffix(target.suffix + '.pre-restore')
    assert safety_copy.exists()


def test_verify_raises_on_a_corrupt_file(tmp_path):
    import pytest
    bad = tmp_path / 'corrupt.db'
    bad.write_bytes(b'not a real sqlite file')
    with pytest.raises(Exception):
        verify(bad)


def test_backup_raises_on_a_missing_source_database(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        backup(tmp_path / 'does-not-exist.db', tmp_path / 'backups')
