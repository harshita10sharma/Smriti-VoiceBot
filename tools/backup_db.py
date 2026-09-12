"""Consistent, safe backup/restore for the runtime SQLite database.

Uses sqlite3's own backup API (not a raw file copy) so a backup taken while
the live service is writing to the database is never torn/inconsistent --
`Connection.backup()` handles that correctly using SQLite's own online
backup mechanism. This never touches runtime code paths: it is a standalone
operator tool, safe to run against a live deployment.

    python tools/backup_db.py backup [--db PATH] [--out DIR]
    python tools/backup_db.py restore --from PATH [--db PATH]
    python tools/backup_db.py verify PATH

The audio cache directory is NOT included: it is a disposable, self-expiring
cache (see AudioStore's retention logic), not source data.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smriti_voice.config import AppConfig  # noqa: E402


def default_db_path() -> Path:
    return Path(AppConfig.load().database_url)


def backup(db_path: Path, out_dir: Path) -> Path:
    if not db_path.exists():
        raise SystemExit(f'No database at {db_path}')
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dest = out_dir / f'smriti-backup-{stamp}.db'

    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    verify(dest)
    print(f'Backup written: {dest}')
    return dest


def restore(backup_path: Path, db_path: Path) -> None:
    if not backup_path.exists():
        raise SystemExit(f'No backup file at {backup_path}')
    verify(backup_path)
    if db_path.exists():
        safety_copy = db_path.with_suffix(db_path.suffix + '.pre-restore')
        shutil.copy2(db_path, safety_copy)
        print(f'Existing database preserved at: {safety_copy}')
    db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, db_path)
    verify(db_path)
    print(f'Restored: {db_path}')


def verify(db_path: Path) -> int:
    """Confirms the file is a real, readable SQLite database and reports
    its schema version -- raises if it's not, so a corrupt/partial backup
    is caught immediately rather than discovered at restore time."""
    connection = sqlite3.connect(str(db_path))
    try:
        (result,) = connection.execute('PRAGMA integrity_check(1)').fetchone()
        if result != 'ok':
            raise SystemExit(f'{db_path} failed integrity check: {result}')
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        tables = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        connection.close()
    print(f'{db_path}: OK, schema version {version}, {tables} tables')
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    p_backup = sub.add_parser('backup')
    p_backup.add_argument('--db', type=Path, default=None)
    p_backup.add_argument('--out', type=Path, default=ROOT / 'backups')

    p_restore = sub.add_parser('restore')
    p_restore.add_argument('--from', dest='from_path', type=Path, required=True)
    p_restore.add_argument('--db', type=Path, default=None)

    p_verify = sub.add_parser('verify')
    p_verify.add_argument('path', type=Path)

    args = parser.parse_args()
    if args.command == 'backup':
        backup(args.db or default_db_path(), args.out)
    elif args.command == 'restore':
        restore(args.from_path, args.db or default_db_path())
    elif args.command == 'verify':
        verify(args.path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
