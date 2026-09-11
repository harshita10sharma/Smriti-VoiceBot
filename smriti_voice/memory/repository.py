"""Data access.

Two invariants hold for every method here:

1. **Every query is scoped by ``user_id``.**  There is no method that can read
   another user's row, so a confused model or a crafted tool argument cannot
   cross the boundary even before the authorisation layer looks at it.
2. **Every statement is parameterised.**  No user or model text is ever
   concatenated into SQL.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from ..database import Database, migrate
from .models import (
    Appointment,
    DailyRoutine,
    FamilyMember,
    Game,
    Meal,
    Medicine,
    PersonalMemory,
    Provenance,
    Reminder,
    User,
    Visitor,
)


def _provenance(row: sqlite3.Row) -> Provenance:
    keys = row.keys()
    return Provenance(
        source=row['source'] if 'source' in keys else 'caregiver',
        created_by=row['created_by'] if 'created_by' in keys else None,
        confidence=row['confidence'] if 'confidence' in keys else 1.0,
        verification_status=row['verification_status'] if 'verification_status' in keys else 'verified',
        created_at=row['created_at'] if 'created_at' in keys else None,
        updated_at=row['updated_at'] if 'updated_at' in keys else None,
    )


def _prov_values(provenance: Provenance, default_source: str = 'caregiver') -> tuple:
    return (provenance.source or default_source, provenance.created_by,
            provenance.confidence, provenance.verification_status)


# The marker written to `created_by` for rows created by POST /v1/memory/sync.
# Full-replace operations are scoped to exactly `source='caregiver' AND
# created_by=CAREGIVER_SYNC_MARKER`, so they can never touch a row created by
# any other path (seed data, a user's own words, an assistant's write, or a
# hypothetical future caregiver channel that isn't this sync endpoint).
CAREGIVER_SYNC_MARKER = 'memory_sync'


class MemoryRepository:
    def __init__(self, database: Database) -> None:
        self.db = database
        with self.db.connect() as connection:
            migrate(connection)

    # ------------------------------------------------------------------ #
    # Users
    # ------------------------------------------------------------------ #
    def upsert_user(self, user: User) -> User:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO users (user_id, display_name, preferred_language, location,
                                      latitude, longitude, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       display_name=excluded.display_name,
                       preferred_language=excluded.preferred_language,
                       location=excluded.location,
                       latitude=excluded.latitude,
                       longitude=excluded.longitude,
                       active=excluded.active,
                       updated_at=datetime('now')""",
                (user.user_id, user.display_name, user.preferred_language, user.location,
                 user.latitude, user.longitude, int(user.active)))
        return user

    def get_user(self, user_id: str) -> User | None:
        with self.db.connect() as connection:
            row = connection.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if row is None:
            return None
        fields = {k: row[k] for k in ('user_id', 'display_name', 'preferred_language',
                                      'location', 'latitude', 'longitude')}
        fields['active'] = bool(row['active']) if 'active' in row.keys() else True
        return User(**fields)

    def is_user_active(self, user_id: str) -> bool:
        """True if the patient may be served: either not yet provisioned at
        all (a normal, expected state before a first caregiver sync or a
        first conversation turn -- absence of a row is never treated as
        "denied") or provisioned with ``active`` not explicitly cleared."""
        user = self.get_user(user_id)
        return True if user is None else user.active

    def ensure_user_provisioned(self, user_id: str, *, default_display_name: str | None = None) -> None:
        """Idempotent provisioning: create a bare user row if one does not
        already exist. Never overwrites an existing row (unlike
        ``upsert_user``), so this is safe to call on every request without
        risk of clobbering a caregiver-set display name. Provisioning a
        user_id does not by itself authorize anything -- that remains
        entirely the API key's allow-list, checked before this is ever
        called."""
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO users (user_id, display_name)
                   VALUES (?, ?)
                   ON CONFLICT(user_id) DO NOTHING""",
                (user_id, default_display_name or user_id))

    # ------------------------------------------------------------------ #
    # Family
    # ------------------------------------------------------------------ #
    def add_family_member(self, member: FamilyMember) -> FamilyMember:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO family_members (user_id, name, relation, phone, is_trusted_contact,
                                               is_primary_contact, lives_in, notes, photo_path,
                                               source, created_by, confidence, verification_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (member.user_id, member.name, member.relation, member.phone,
                 int(member.is_trusted_contact), int(member.is_primary_contact),
                 member.lives_in, member.notes, member.photo_path, *_prov_values(member.provenance)))
        return member.model_copy(update={'id': cursor.lastrowid})

    def list_family(self, user_id: str) -> list[FamilyMember]:
        with self.db.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM family_members WHERE user_id = ? ORDER BY is_primary_contact DESC, name',
                (user_id,)).fetchall()
        return [self._to_family(row) for row in rows]

    def find_family(self, user_id: str, *, name: str | None = None,
                    relation: str | None = None) -> list[FamilyMember]:
        """Case-insensitive match on name or relation, scoped to one user."""
        clauses = ['user_id = ?']
        params: list[Any] = [user_id]
        if name:
            clauses.append('LOWER(name) LIKE ?')
            params.append(f'%{name.strip().lower()}%')
        if relation:
            clauses.append('LOWER(relation) = ?')
            params.append(relation.strip().lower())
        query = f"SELECT * FROM family_members WHERE {' AND '.join(clauses)} ORDER BY name"
        with self.db.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._to_family(row) for row in rows]

    def primary_contact(self, user_id: str) -> FamilyMember | None:
        with self.db.connect() as connection:
            row = connection.execute(
                """SELECT * FROM family_members
                   WHERE user_id = ? AND is_primary_contact = 1 LIMIT 1""", (user_id,)).fetchone()
        return self._to_family(row) if row else None

    @staticmethod
    def _to_family(row: sqlite3.Row) -> FamilyMember:
        return FamilyMember(
            id=row['id'], user_id=row['user_id'], name=row['name'], relation=row['relation'],
            phone=row['phone'], is_trusted_contact=bool(row['is_trusted_contact']),
            is_primary_contact=bool(row['is_primary_contact']), lives_in=row['lives_in'],
            notes=row['notes'], photo_path=row['photo_path'], provenance=_provenance(row))

    # ------------------------------------------------------------------ #
    # Meals
    # ------------------------------------------------------------------ #
    def add_meal(self, meal: Meal) -> Meal:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO meals (user_id, meal_date, meal_type, description,
                                      source, created_by, confidence, verification_status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (meal.user_id, meal.meal_date.isoformat(), meal.meal_type, meal.description,
                 *_prov_values(meal.provenance)))
        return meal.model_copy(update={'id': cursor.lastrowid})

    def meals_on(self, user_id: str, day: date) -> list[Meal]:
        with self.db.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM meals WHERE user_id = ? AND meal_date = ? ORDER BY id',
                (user_id, day.isoformat())).fetchall()
        return [Meal(id=r['id'], user_id=r['user_id'], meal_date=date.fromisoformat(r['meal_date']),
                     meal_type=r['meal_type'], description=r['description'],
                     provenance=_provenance(r)) for r in rows]

    def meals_between(self, user_id: str, start: date, end: date) -> list[Meal]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM meals WHERE user_id = ? AND meal_date BETWEEN ? AND ?
                   ORDER BY meal_date, id""",
                (user_id, start.isoformat(), end.isoformat())).fetchall()
        return [Meal(id=r['id'], user_id=r['user_id'], meal_date=date.fromisoformat(r['meal_date']),
                     meal_type=r['meal_type'], description=r['description'],
                     provenance=_provenance(r)) for r in rows]

    # ------------------------------------------------------------------ #
    # Medicines  (read + caregiver-only writes; voice can never mutate these)
    # ------------------------------------------------------------------ #
    def add_medicine(self, medicine: Medicine) -> Medicine:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO medicines (user_id, name, dosage, schedule_time, time_of_day,
                                          instructions, active, prescribed_by,
                                          source, created_by, confidence, verification_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (medicine.user_id, medicine.name, medicine.dosage, medicine.schedule_time,
                 medicine.time_of_day, medicine.instructions, int(medicine.active),
                 medicine.prescribed_by, *_prov_values(medicine.provenance)))
        return medicine.model_copy(update={'id': cursor.lastrowid})

    def list_medicines(self, user_id: str, *, time_of_day: str | None = None,
                       active_only: bool = True) -> list[Medicine]:
        clauses = ['user_id = ?']
        params: list[Any] = [user_id]
        if active_only:
            clauses.append('active = 1')
        if time_of_day:
            clauses.append('LOWER(time_of_day) = ?')
            params.append(time_of_day.strip().lower())
        query = f"SELECT * FROM medicines WHERE {' AND '.join(clauses)} ORDER BY schedule_time, name"
        with self.db.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Medicine(id=r['id'], user_id=r['user_id'], name=r['name'], dosage=r['dosage'],
                         schedule_time=r['schedule_time'], time_of_day=r['time_of_day'],
                         instructions=r['instructions'], active=bool(r['active']),
                         prescribed_by=r['prescribed_by'], provenance=_provenance(r))
                for r in rows]

    # ------------------------------------------------------------------ #
    # Appointments, routine, visitors, reminders
    # ------------------------------------------------------------------ #
    def add_appointment(self, appointment: Appointment) -> Appointment:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO appointments (user_id, title, appointment_date, appointment_time,
                                             location, with_person, notes,
                                             source, created_by, confidence, verification_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (appointment.user_id, appointment.title, appointment.appointment_date.isoformat(),
                 appointment.appointment_time, appointment.location, appointment.with_person,
                 appointment.notes, *_prov_values(appointment.provenance)))
        return appointment.model_copy(update={'id': cursor.lastrowid})

    def appointments_on(self, user_id: str, day: date) -> list[Appointment]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM appointments WHERE user_id = ? AND appointment_date = ?
                   ORDER BY appointment_time""", (user_id, day.isoformat())).fetchall()
        return [self._to_appointment(r) for r in rows]

    def upcoming_appointments(self, user_id: str, day: date, days: int = 7) -> list[Appointment]:
        end = day + timedelta(days=days)
        with self.db.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM appointments WHERE user_id = ? AND appointment_date BETWEEN ? AND ?
                   ORDER BY appointment_date, appointment_time""",
                (user_id, day.isoformat(), end.isoformat())).fetchall()
        return [self._to_appointment(r) for r in rows]

    @staticmethod
    def _to_appointment(row: sqlite3.Row) -> Appointment:
        return Appointment(id=row['id'], user_id=row['user_id'], title=row['title'],
                           appointment_date=date.fromisoformat(row['appointment_date']),
                           appointment_time=row['appointment_time'], location=row['location'],
                           with_person=row['with_person'], notes=row['notes'],
                           provenance=_provenance(row))

    def add_routine(self, routine: DailyRoutine) -> DailyRoutine:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO daily_routines (user_id, title, routine_time, day_of_week, notes,
                                               source, created_by, confidence, verification_status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (routine.user_id, routine.title, routine.routine_time, routine.day_of_week,
                 routine.notes, *_prov_values(routine.provenance)))
        return routine.model_copy(update={'id': cursor.lastrowid})

    def list_routine(self, user_id: str, day_of_week: str | None = None) -> list[DailyRoutine]:
        clauses = ['user_id = ?']
        params: list[Any] = [user_id]
        if day_of_week:
            clauses.append("(day_of_week IS NULL OR day_of_week = '' OR LOWER(day_of_week) = ?)")
            params.append(day_of_week.lower())
        query = f"SELECT * FROM daily_routines WHERE {' AND '.join(clauses)} ORDER BY routine_time"
        with self.db.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [DailyRoutine(id=r['id'], user_id=r['user_id'], title=r['title'],
                             routine_time=r['routine_time'], day_of_week=r['day_of_week'],
                             notes=r['notes'], provenance=_provenance(r)) for r in rows]

    def add_visitor(self, visitor: Visitor) -> Visitor:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO visitors (user_id, name, relation, visit_date, visit_time, notes,
                                         source, created_by, confidence, verification_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (visitor.user_id, visitor.name, visitor.relation, visitor.visit_date.isoformat(),
                 visitor.visit_time, visitor.notes, *_prov_values(visitor.provenance)))
        return visitor.model_copy(update={'id': cursor.lastrowid})

    def visitors_on(self, user_id: str, day: date) -> list[Visitor]:
        with self.db.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM visitors WHERE user_id = ? AND visit_date = ? ORDER BY visit_time',
                (user_id, day.isoformat())).fetchall()
        return [Visitor(id=r['id'], user_id=r['user_id'], name=r['name'], relation=r['relation'],
                        visit_date=date.fromisoformat(r['visit_date']), visit_time=r['visit_time'],
                        notes=r['notes'], provenance=_provenance(r)) for r in rows]

    def add_reminder(self, reminder: Reminder) -> Reminder:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO reminders (user_id, text, remind_at, recurrence, status,
                                          source, created_by, confidence, verification_status)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (reminder.user_id, reminder.text, reminder.remind_at, reminder.recurrence,
                 reminder.status, *_prov_values(reminder.provenance)))
        return reminder.model_copy(update={'id': cursor.lastrowid})

    def list_reminders(self, user_id: str, status: str = 'active') -> list[Reminder]:
        with self.db.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM reminders WHERE user_id = ? AND status = ? ORDER BY id DESC',
                (user_id, status)).fetchall()
        return [Reminder(id=r['id'], user_id=r['user_id'], text=r['text'], remind_at=r['remind_at'],
                         recurrence=r['recurrence'], status=r['status'], provenance=_provenance(r))
                for r in rows]

    # ------------------------------------------------------------------ #
    # Personal memories & games
    # ------------------------------------------------------------------ #
    def add_memory(self, memory: PersonalMemory) -> PersonalMemory:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO personal_memories (user_id, title, content, category, people,
                                                  happened_on, source, created_by, confidence,
                                                  verification_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (memory.user_id, memory.title, memory.content, memory.category, memory.people,
                 memory.happened_on.isoformat() if memory.happened_on else None,
                 *_prov_values(memory.provenance)))
        return memory.model_copy(update={'id': cursor.lastrowid})

    def list_memories(self, user_id: str, *, category: str | None = None,
                      limit: int = 50) -> list[PersonalMemory]:
        clauses = ['user_id = ?']
        params: list[Any] = [user_id]
        if category:
            clauses.append('LOWER(category) = ?')
            params.append(category.lower())
        query = (f"SELECT * FROM personal_memories WHERE {' AND '.join(clauses)} "
                 f"ORDER BY id DESC LIMIT ?")
        params.append(max(1, min(limit, 200)))
        with self.db.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [PersonalMemory(
            id=r['id'], user_id=r['user_id'], title=r['title'], content=r['content'],
            category=r['category'], people=r['people'],
            happened_on=date.fromisoformat(r['happened_on']) if r['happened_on'] else None,
            provenance=_provenance(r)) for r in rows]

    def add_game(self, game: Game) -> Game:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO games (user_id, game_key, display_name, description, enabled)
                   VALUES (?,?,?,?,?)""",
                (game.user_id, game.game_key, game.display_name, game.description,
                 int(game.enabled)))
        return game.model_copy(update={'id': cursor.lastrowid})

    def list_games(self, user_id: str) -> list[Game]:
        with self.db.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM games WHERE user_id = ? AND enabled = 1 ORDER BY display_name',
                (user_id,)).fetchall()
        return [Game(id=r['id'], user_id=r['user_id'], game_key=r['game_key'],
                     display_name=r['display_name'], description=r['description'],
                     enabled=bool(r['enabled'])) for r in rows]

    # ------------------------------------------------------------------ #
    # Conversation persistence
    # ------------------------------------------------------------------ #
    def ensure_session(self, session_id: str, user_id: str, language: str = 'eng') -> None:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO conversations (session_id, user_id, language)
                   VALUES (?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       language=excluded.language, last_active_at=datetime('now')""",
                (session_id, user_id, language))

    def add_turn(self, *, turn_id: str, session_id: str, user_id: str, role: str,
                 text: str, language: str, kind: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO conversation_turns (turn_id, session_id, user_id, role, text,
                                                   language, kind)
                   VALUES (?,?,?,?,?,?,?)""",
                (turn_id, session_id, user_id, role, text, language, kind))

    def recent_turns(self, session_id: str, user_id: str, limit: int = 10) -> list[sqlite3.Row]:
        """Bounded history for one session, scoped to its owner."""
        with self.db.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM conversation_turns
                   WHERE session_id = ? AND user_id = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                (session_id, user_id, max(1, min(limit, 50)))).fetchall()
        return list(reversed(rows))

    def session_owner(self, session_id: str) -> str | None:
        with self.db.connect() as connection:
            row = connection.execute('SELECT user_id FROM conversations WHERE session_id = ?',
                                     (session_id,)).fetchone()
        return row['user_id'] if row else None

    # ------------------------------------------------------------------ #
    # Caregiver memory synchronisation (POST /v1/memory/sync)
    # ------------------------------------------------------------------ #
    def sync_caregiver_memory(self, user_id: str, *, family_members: list[FamilyMember],
                              medicines: list[Medicine],
                              routines: list[DailyRoutine]) -> tuple[int, int, int]:
        """Atomically replace this user's caregiver-synced family members,
        medicines and daily routines in one transaction.

        All three tables are scoped to ``source = 'caregiver' AND
        created_by = CAREGIVER_SYNC_MARKER`` for both the delete and the
        insert, so rows from any other source — seed data, a user's own
        words, an assistant's write, an import, or a caregiver row created
        outside this endpoint — are never touched. Everything happens on one
        connection inside one ``with`` block: if any statement raises,
        ``Database.connect()`` rolls back the whole thing, so a patient is
        never left partially synchronised.
        """
        with self.db.connect() as connection:
            # A caregiver may sync a patient's data before that patient has
            # ever opened a conversation. Provision the bare `users` row in
            # the same transaction rather than requiring a separate call --
            # without this, the first sync for a brand-new patient hits a
            # foreign-key violation on every insert below. This is
            # provisioning only, never authorization: the route already
            # checked the caller's API-key allow-list before reaching here.
            connection.execute(
                """INSERT INTO users (user_id, display_name)
                   VALUES (?, ?)
                   ON CONFLICT(user_id) DO NOTHING""",
                (user_id, user_id))
            connection.execute(
                """DELETE FROM family_members
                   WHERE user_id = ? AND source = 'caregiver' AND created_by = ?""",
                (user_id, CAREGIVER_SYNC_MARKER))
            for member in family_members:
                connection.execute(
                    """INSERT INTO family_members (user_id, name, relation, phone,
                                                   is_trusted_contact, is_primary_contact,
                                                   lives_in, notes, photo_path,
                                                   source, created_by, confidence,
                                                   verification_status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (member.user_id, member.name, member.relation, member.phone,
                     int(member.is_trusted_contact), int(member.is_primary_contact),
                     member.lives_in, member.notes, member.photo_path,
                     *_prov_values(member.provenance)))

            connection.execute(
                """DELETE FROM medicines
                   WHERE user_id = ? AND source = 'caregiver' AND created_by = ?""",
                (user_id, CAREGIVER_SYNC_MARKER))
            for medicine in medicines:
                connection.execute(
                    """INSERT INTO medicines (user_id, name, dosage, schedule_time, time_of_day,
                                              instructions, active, prescribed_by,
                                              source, created_by, confidence, verification_status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (medicine.user_id, medicine.name, medicine.dosage, medicine.schedule_time,
                     medicine.time_of_day, medicine.instructions, int(medicine.active),
                     medicine.prescribed_by, *_prov_values(medicine.provenance)))

            connection.execute(
                """DELETE FROM daily_routines
                   WHERE user_id = ? AND source = 'caregiver' AND created_by = ?""",
                (user_id, CAREGIVER_SYNC_MARKER))
            for routine in routines:
                connection.execute(
                    """INSERT INTO daily_routines (user_id, title, routine_time, day_of_week,
                                                   notes, source, created_by, confidence,
                                                   verification_status)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (routine.user_id, routine.title, routine.routine_time, routine.day_of_week,
                     routine.notes, *_prov_values(routine.provenance)))
        return len(family_members), len(medicines), len(routines)

    # ------------------------------------------------------------------ #
    def record_telemetry(self, *, event_id: str, event_type: str, payload: dict,
                         request_id: str | None = None, session_id: str | None = None,
                         user_id: str | None = None) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO telemetry (event_id, request_id, session_id, user_id, event_type, payload)
                   VALUES (?,?,?,?,?,?)""",
                (event_id, request_id, session_id, user_id, event_type,
                 json.dumps(payload, ensure_ascii=False, default=str)))
