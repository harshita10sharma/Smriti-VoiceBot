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
    # -- 3 ------------------------------------------------------------------
    # Async TTS jobs for the voice endpoint. Persisted (not an in-memory
    # dict) so job state survives a process restart and a status poll always
    # reflects what actually happened, not what a dead process remembered.
    """
    CREATE TABLE IF NOT EXISTS voice_jobs (
        job_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        session_id TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        language TEXT NOT NULL,
        response_text TEXT NOT NULL,
        audio_id TEXT,
        tts_provider TEXT,
        error_code TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_voice_jobs_user ON voice_jobs(user_id);
    """,
    # -- 4 ------------------------------------------------------------------
    # `active`: a caregiver/backend-disabled patient must be denied even
    # though their API-key allow-list membership is unchanged (authorization
    # and provisioning are deliberately separate concerns -- see
    # MemoryRepository.is_user_active). Defaults to 1 so every existing row
    # is unaffected by this upgrade.
    #
    # `idempotent_requests`: optional request-level idempotency for
    # POST /v1/conversation and POST /v1/conversation/voice (see
    # smriti_voice/idempotency.py). A row is claimed atomically before any
    # work happens, so two concurrent identical requests can never both
    # execute a controlled action.
    """
    ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1;

    CREATE TABLE IF NOT EXISTS idempotent_requests (
        user_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        response_json TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (user_id, idempotency_key)
    );
    CREATE INDEX IF NOT EXISTS idx_idempotent_created ON idempotent_requests(created_at);
    """,
    # -- 5 ------------------------------------------------------------------
    # Backend-integration fields, all additive and backward compatible:
    #
    # `external_id` (users/family_members/medicines/daily_routines): a stable
    # reference the backend's own database assigns (e.g. a Supabase UUID or
    # row id), independent of this database's own autoincrement `id`. Never
    # required -- existing rows and callers that never set it are unaffected.
    #
    # `timezone` (users): IANA name, used for day-of-week medicine schedule
    # matching (see MemoryService).
    #
    # `memory_prompt`/`is_deceased` (family_members): passed straight through
    # to the model as context, never inferred.
    #
    # `chosen_time_min`/`window_start_min`/`window_end_min`/`days_of_week`
    # (medicines): structured schedule data (minutes-from-midnight, ISO
    # weekdays "1".."7", Monday=1) alongside the existing free-text
    # `schedule_time`/`time_of_day`, which remain the fallback when a medicine
    # has no structured schedule.
    #
    # `memory_sync_state`: one row per patient recording the last-applied
    # POST /v1/memory/sync revision and a content hash, so a repeated,
    # stale, or conflicting revision can be detected (see
    # MemoryRepository.sync_caregiver_memory). A patient with no row here
    # has never been synced with a revision at all -- unversioned callers
    # (source_revision omitted) never create or check this row, so existing
    # integrations are completely unaffected.
    """
    ALTER TABLE users ADD COLUMN external_id TEXT;
    ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata';
    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_id ON users(external_id)
        WHERE external_id IS NOT NULL;

    ALTER TABLE family_members ADD COLUMN external_id TEXT;
    ALTER TABLE family_members ADD COLUMN memory_prompt TEXT;
    ALTER TABLE family_members ADD COLUMN is_deceased INTEGER NOT NULL DEFAULT 0;
    CREATE UNIQUE INDEX IF NOT EXISTS idx_family_external_id
        ON family_members(user_id, external_id) WHERE external_id IS NOT NULL;

    ALTER TABLE medicines ADD COLUMN external_id TEXT;
    ALTER TABLE medicines ADD COLUMN chosen_time_min INTEGER;
    ALTER TABLE medicines ADD COLUMN window_start_min INTEGER;
    ALTER TABLE medicines ADD COLUMN window_end_min INTEGER;
    ALTER TABLE medicines ADD COLUMN days_of_week TEXT;
    CREATE UNIQUE INDEX IF NOT EXISTS idx_medicines_external_id
        ON medicines(user_id, external_id) WHERE external_id IS NOT NULL;

    ALTER TABLE daily_routines ADD COLUMN external_id TEXT;
    CREATE UNIQUE INDEX IF NOT EXISTS idx_routines_external_id
        ON daily_routines(user_id, external_id) WHERE external_id IS NOT NULL;

    CREATE TABLE IF NOT EXISTS memory_sync_state (
        user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
        source_revision INTEGER NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        content_hash TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # -- 6 ------------------------------------------------------------------
    # Makes session ownership and pending-confirmation state survive a
    # process restart. The `conversations` table already durably records
    # every session (session_id, user_id, language) and every turn lives in
    # `conversation_turns` -- this only adds the two pieces that used to
    # live purely in the in-memory SessionStore: the currently-pending
    # confirmation (as JSON; NULL when nothing is pending) and the last
    # subject mentioned (for pronoun resolution). No new table: reusing the
    # existing session record rather than inventing a parallel one.
    """
    ALTER TABLE conversations ADD COLUMN pending_json TEXT;
    ALTER TABLE conversations ADD COLUMN last_subject TEXT;
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
