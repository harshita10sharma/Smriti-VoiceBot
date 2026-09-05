"""Schema migrations.

Migrations are an ordered list of SQL scripts.  ``schema_version`` in the
``PRAGMA user_version`` slot records how far a database has been migrated, so an
existing device database is upgraded in place rather than recreated.

Provenance is a first-class part of the schema: every table that stores a
personal fact carries ``source``, ``created_by``, ``confidence`` and
``verification_status``.  A fact written by the assistant is stored as
``source='assistant'``/``verification_status='unverified'`` and the read tools
mark it as unverified when they hand it to the model.
"""
from __future__ import annotations

import sqlite3

# Common provenance columns, repeated so each CREATE TABLE stays readable.
_PROVENANCE = """
    source TEXT NOT NULL DEFAULT 'caregiver',
    created_by TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    verification_status TEXT NOT NULL DEFAULT 'verified',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
"""

MIGRATIONS: list[str] = [
    # -- 1 ------------------------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        preferred_language TEXT NOT NULL DEFAULT 'eng',
        location TEXT,
        latitude REAL,
        longitude REAL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS family_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        relation TEXT NOT NULL,
        phone TEXT,
        is_trusted_contact INTEGER NOT NULL DEFAULT 0,
        is_primary_contact INTEGER NOT NULL DEFAULT 0,
        lives_in TEXT,
        notes TEXT,
        photo_path TEXT,
        {_PROVENANCE}
    );
    CREATE INDEX IF NOT EXISTS idx_family_user ON family_members(user_id);

    CREATE TABLE IF NOT EXISTS personal_memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'general',
        people TEXT,
        happened_on TEXT,
        {_PROVENANCE}
    );
    CREATE INDEX IF NOT EXISTS idx_memories_user ON personal_memories(user_id);

    CREATE TABLE IF NOT EXISTS meals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        meal_date TEXT NOT NULL,
        meal_type TEXT NOT NULL,
        description TEXT NOT NULL,
        {_PROVENANCE}
    );
    CREATE INDEX IF NOT EXISTS idx_meals_user_date ON meals(user_id, meal_date);

    CREATE TABLE IF NOT EXISTS medicines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        dosage TEXT,
        schedule_time TEXT,
        time_of_day TEXT,
        instructions TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        prescribed_by TEXT,
        {_PROVENANCE}
    );
    CREATE INDEX IF NOT EXISTS idx_medicines_user ON medicines(user_id);

    CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        appointment_date TEXT NOT NULL,
        appointment_time TEXT,
        location TEXT,
        with_person TEXT,
        notes TEXT,
        {_PROVENANCE}
    );
    CREATE INDEX IF NOT EXISTS idx_appointments_user_date ON appointments(user_id, appointment_date);

    CREATE TABLE IF NOT EXISTS daily_routines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        routine_time TEXT,
        day_of_week TEXT,
        notes TEXT,
        {_PROVENANCE}
    );
    CREATE INDEX IF NOT EXISTS idx_routines_user ON daily_routines(user_id);

    CREATE TABLE IF NOT EXISTS visitors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        relation TEXT,
        visit_date TEXT NOT NULL,
        visit_time TEXT,
        notes TEXT,
        {_PROVENANCE}
    );
    CREATE INDEX IF NOT EXISTS idx_visitors_user_date ON visitors(user_id, visit_date);

    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        remind_at TEXT,
        recurrence TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        {_PROVENANCE}
    );
    CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id);

    CREATE TABLE IF NOT EXISTS preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        {_PROVENANCE},
        UNIQUE(user_id, key)
    );

    CREATE TABLE IF NOT EXISTS conversations (
        session_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        language TEXT NOT NULL DEFAULT 'eng',
        started_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_active_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);

    CREATE TABLE IF NOT EXISTS conversation_turns (
        turn_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES conversations(session_id) ON DELETE CASCADE,
        user_id TEXT NOT NULL,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        language TEXT NOT NULL DEFAULT 'eng',
        kind TEXT NOT NULL DEFAULT 'CONVERSATION',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_turns_session ON conversation_turns(session_id, created_at);

    CREATE TABLE IF NOT EXISTS telemetry (
        event_id TEXT PRIMARY KEY,
        request_id TEXT,
        session_id TEXT,
        user_id TEXT,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS sync_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        entity TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_outbox_status ON sync_outbox(status);
    """,
    # -- 2 ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        game_key TEXT NOT NULL,
        display_name TEXT NOT NULL,
        description TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id);
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def migrate(connection: sqlite3.Connection) -> int:
    """Apply pending migrations.  Returns the resulting schema version."""
    current = connection.execute('PRAGMA user_version').fetchone()[0]
    for index, script in enumerate(MIGRATIONS, start=1):
        if index > current:
            connection.executescript(script)
            connection.execute(f'PRAGMA user_version = {index}')
    return SCHEMA_VERSION
