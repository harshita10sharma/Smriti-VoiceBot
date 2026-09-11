"""Schema migration 4 (users.active, idempotent_requests) applies safely and
in place against a database that already exists at an older schema version --
the exact upgrade path a real deployed device database goes through. This
does not merely start from a fresh :memory: database (every other test's
`app` fixture does exactly that): it builds a real on-disk file at schema
version 3 first, the way a pre-upgrade device actually looks, and confirms
migrating it forward neither loses data nor breaks the version gate.
"""
from __future__ import annotations

import sqlite3

from smriti_voice.database.migrations import MIGRATIONS, SCHEMA_VERSION, migrate


def test_schema_version_is_four_after_this_change():
    assert SCHEMA_VERSION == len(MIGRATIONS) == 4


def test_migrating_a_fresh_database_reaches_current_version():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    version = migrate(connection)
    assert version == SCHEMA_VERSION
    assert connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


def test_upgrading_an_existing_version_3_database_in_place_preserves_data():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    # Apply only migrations 1-3, exactly what a device shipped before this
    # change would already have on disk.
    for index, script in enumerate(MIGRATIONS[:3], start=1):
        connection.executescript(script)
        connection.execute(f'PRAGMA user_version = {index}')

    connection.execute(
        "INSERT INTO users (user_id, display_name) VALUES ('pre-existing-user', 'Someone')")
    connection.commit()

    version = migrate(connection)
    assert version == SCHEMA_VERSION

    row = connection.execute(
        "SELECT * FROM users WHERE user_id = 'pre-existing-user'").fetchone()
    assert row is not None
    # A pre-existing row gets the new column's default, not NULL/an error.
    assert row['active'] == 1

    # The new table exists and is empty (not populated with anything
    # invented for old data).
    count = connection.execute('SELECT COUNT(*) FROM idempotent_requests').fetchone()[0]
    assert count == 0


def test_migrating_twice_is_a_safe_no_op():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    migrate(connection)
    migrate(connection)  # must not re-run ALTER TABLE ADD COLUMN and error
    assert connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION


def test_active_column_and_idempotent_requests_table_exist(app):
    """Through the real MemoryRepository/Application wiring, not just the
    raw migration function."""
    with app.memory.repo.db.connect() as connection:
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(users)')}
        assert 'active' in columns
        tables = {row['name'] for row in
                  connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'idempotent_requests' in tables
