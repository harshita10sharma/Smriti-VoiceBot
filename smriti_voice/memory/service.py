"""The application-facing memory API.

Tools call this, not the repository, so date handling ("yesterday"), trust rules
and formatting live in one place.  Nothing here formats a *sentence* — that is
the model's job, in the user's language.  This layer returns structured facts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..safety.prompt_injection import sanitise_untrusted
from .models import FamilyMember, PersonalMemory
from .rag import LexicalMemoryRetriever, RetrievedMemory
from .repository import MemoryRepository

DEFAULT_TZ = 'Asia/Kolkata'

RELATIVE_DAYS = {
    'today': 0, 'yesterday': -1, 'tomorrow': 1,
    'day before yesterday': -2, 'day after tomorrow': 2,
}


@dataclass(frozen=True)
class ResolvedDate:
    value: date
    label: str


def _format_minutes(minutes: int | None) -> str | None:
    """0-1439 minutes-from-midnight -> 'HH:MM', or None if unset."""
    if minutes is None:
        return None
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def resolve_date(expression: str | None, *, today: date | None = None) -> ResolvedDate:
    """Turn 'yesterday' or '2026-09-03' into a date.  Unknown input → today."""
    reference = today or datetime.now(ZoneInfo(DEFAULT_TZ)).date()
    raw = (expression or 'today').strip().lower()
    if raw in RELATIVE_DAYS:
        return ResolvedDate(reference + timedelta(days=RELATIVE_DAYS[raw]), raw)
    try:
        return ResolvedDate(date.fromisoformat(raw), raw)
    except ValueError:
        return ResolvedDate(reference, 'today')


class MemoryService:
    def __init__(self, repository: MemoryRepository, *, timezone: str = DEFAULT_TZ) -> None:
        self.repo = repository
        self.tz = ZoneInfo(timezone)
        self.retriever = LexicalMemoryRetriever()

    # ------------------------------------------------------------------ #
    def today(self) -> date:
        return datetime.now(self.tz).date()

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def _tz_for(self, user_id: str) -> ZoneInfo:
        """The patient's own stored IANA timezone (see users.timezone,
        synced via POST /v1/memory/sync), not the single service-wide
        default. Falls back to the service default for a user with no row,
        an empty timezone, or an invalid/unrecognized IANA name -- a typo
        in synced data must never crash a query, only silently fall back."""
        user = self.repo.get_user(user_id)
        if user and user.timezone:
            try:
                return ZoneInfo(user.timezone)
            except Exception:
                pass
        return self.tz

    def _today_for(self, user_id: str) -> date:
        """'Today' resolved in the patient's own timezone -- what decides
        whether a medicine's days_of_week or an appointment's date matches
        "tonight"/"tomorrow" for *this* patient, not for whichever
        timezone the service process happens to be configured with."""
        return datetime.now(self._tz_for(user_id)).date()

    # ------------------------------------------------------------------ #
    # Family
    # ------------------------------------------------------------------ #
    def find_family(self, user_id: str, *, name: str | None = None,
                    relation: str | None = None) -> list[dict]:
        members = self.repo.find_family(user_id, name=name, relation=relation)
        return [self._family_payload(member) for member in members]

    def list_family(self, user_id: str) -> list[dict]:
        return [self._family_payload(member) for member in self.repo.list_family(user_id)]

    def trusted_contact(self, user_id: str, name: str | None = None) -> FamilyMember | None:
        """A contact may only be dialled if the caregiver marked it trusted."""
        candidates = (self.repo.find_family(user_id, name=name) if name
                      else self.repo.list_family(user_id))
        for member in candidates:
            if member.is_trusted_contact and member.phone:
                return member
        return None

    @staticmethod
    def _family_payload(member: FamilyMember) -> dict:
        # The phone number is deliberately NOT returned to the model.  The model
        # never needs it: dialling happens through a controlled action by member id.
        return {
            'id': member.id,
            'name': member.name,
            'relation': member.relation,
            'lives_in': member.lives_in,
            'notes': sanitise_untrusted(member.notes or ''),
            'memory_prompt': sanitise_untrusted(member.memory_prompt or '') or None,
            # Passed straight through -- never inferred from absence or from
            # conversation. The system prompt instructs the model not to
            # speak of a deceased person as available to call, visit or
            # reply, based on this flag alone.
            'is_deceased': member.is_deceased,
            'is_trusted_contact': member.is_trusted_contact,
            'is_primary_contact': member.is_primary_contact,
            'has_phone_number': bool(member.phone),
            'verification_status': member.provenance.verification_status,
            'source': member.provenance.source,
        }

    # ------------------------------------------------------------------ #
    # Meals, medicine, schedule
    # ------------------------------------------------------------------ #
    def meals(self, user_id: str, when: str | None = 'today') -> dict:
        resolved = resolve_date(when, today=self._today_for(user_id))
        meals = self.repo.meals_on(user_id, resolved.value)
        return {
            'date': resolved.value.isoformat(),
            'requested': resolved.label,
            'meals': [{'meal_type': meal.meal_type,
                       'description': sanitise_untrusted(meal.description),
                       'source': meal.provenance.source,
                       'verification_status': meal.provenance.verification_status}
                      for meal in meals],
            'count': len(meals),
        }

    def medicines(self, user_id: str, time_of_day: str | None = None,
                  *, when: str | None = None) -> dict:
        """``when`` ('today'/'tomorrow'/an ISO date) filters by
        ``days_of_week`` for medicines that have a structured schedule (see
        MemorySyncMedicine). A medicine with no ``days_of_week`` set is
        never filtered out by day -- exactly the old behaviour, for any
        medicine synced before this field existed. Day resolution reuses
        ``resolve_date`` (the same helper ``meals``/``schedule`` use), so
        the "today/tomorrow" vocabulary is identical everywhere, not
        reimplemented per tool.

        This never infers whether a dose was actually taken -- there is no
        adherence data source in this repository at all, so nothing here
        could report it even if asked to; the system prompt separately
        instructs the model never to claim adherence information it wasn't
        given.
        """
        medicines = self.repo.list_medicines(user_id, time_of_day=time_of_day)
        resolved = resolve_date(when, today=self._today_for(user_id)) if when else None
        iso_weekday = str(resolved.value.isoweekday()) if resolved else None

        def _matches_day(m) -> bool:
            if iso_weekday is None or not m.days_of_week:
                return True  # no day filter requested, or medicine has no structured days
            days = {d.strip() for d in m.days_of_week.split(',') if d.strip()}
            return iso_weekday in days

        filtered = [m for m in medicines if _matches_day(m)]

        return {
            'time_of_day': time_of_day,
            'date': resolved.value.isoformat() if resolved else None,
            'requested': resolved.label if resolved else None,
            'medicines': [{'name': m.name, 'dosage': m.dosage, 'schedule_time': m.schedule_time,
                           'time_of_day': m.time_of_day,
                           'instructions': sanitise_untrusted(m.instructions or ''),
                           'prescribed_by': m.prescribed_by,
                           'external_id': m.external_id,
                           'chosen_time': _format_minutes(m.chosen_time_min),
                           'window_start': _format_minutes(m.window_start_min),
                           'window_end': _format_minutes(m.window_end_min),
                           'days_of_week': m.days_of_week,
                           'verification_status': m.provenance.verification_status}
                          for m in filtered],
            'count': len(filtered),
            'read_only': True,
            'note': 'Medication information is read-only. Changes require a caregiver or doctor. '
                    'Whether a dose was actually taken is not tracked here -- say so honestly if asked.',
        }

    def schedule(self, user_id: str, when: str | None = 'today') -> dict:
        resolved = resolve_date(when, today=self._today_for(user_id))
        weekday = resolved.value.strftime('%A').lower()
        routines = self.repo.list_routine(user_id, day_of_week=weekday)
        appointments = self.repo.appointments_on(user_id, resolved.value)
        visitors = self.repo.visitors_on(user_id, resolved.value)
        medicines = self.repo.list_medicines(user_id)
        return {
            'date': resolved.value.isoformat(),
            'weekday': weekday,
            'routine': [{'title': r.title, 'time': r.routine_time,
                         'notes': sanitise_untrusted(r.notes or '')} for r in routines],
            'appointments': [{'title': a.title, 'time': a.appointment_time,
                              'location': a.location, 'with_person': a.with_person}
                             for a in appointments],
            'visitors': [{'name': v.name, 'relation': v.relation, 'time': v.visit_time}
                         for v in visitors],
            'medicine_times': [{'name': m.name, 'time': m.schedule_time,
                                'time_of_day': m.time_of_day} for m in medicines],
        }

    def appointments(self, user_id: str, when: str | None = 'today', days: int = 0) -> dict:
        resolved = resolve_date(when, today=self._today_for(user_id))
        items = (self.repo.upcoming_appointments(user_id, resolved.value, days) if days
                 else self.repo.appointments_on(user_id, resolved.value))
        return {
            'date': resolved.value.isoformat(),
            'appointments': [{'title': a.title, 'date': a.appointment_date.isoformat(),
                              'time': a.appointment_time, 'location': a.location,
                              'with_person': a.with_person,
                              'notes': sanitise_untrusted(a.notes or '')} for a in items],
            'count': len(items),
        }

    def visitors(self, user_id: str, when: str | None = 'today') -> dict:
        resolved = resolve_date(when, today=self._today_for(user_id))
        items = self.repo.visitors_on(user_id, resolved.value)
        return {'date': resolved.value.isoformat(),
                'visitors': [{'name': v.name, 'relation': v.relation, 'time': v.visit_time,
                              'notes': sanitise_untrusted(v.notes or '')} for v in items],
                'count': len(items)}

    def reminders(self, user_id: str) -> dict:
        items = self.repo.list_reminders(user_id)
        return {'reminders': [{'text': sanitise_untrusted(r.text), 'remind_at': r.remind_at,
                               'recurrence': r.recurrence} for r in items],
                'count': len(items)}

    def games(self, user_id: str) -> dict:
        items = self.repo.list_games(user_id)
        return {'games': [{'key': g.game_key, 'name': g.display_name,
                           'description': g.description} for g in items],
                'count': len(items)}

    # ------------------------------------------------------------------ #
    # Unstructured memories (RAG)
    # ------------------------------------------------------------------ #
    def search_memories(self, user_id: str, query: str, *, limit: int = 3) -> dict:
        memories = self.repo.list_memories(user_id, limit=200)
        hits: list[RetrievedMemory] = self.retriever.search(query, memories, limit=limit)
        return {
            'query': query,
            'results': [{'title': hit.memory.title,
                         'content': sanitise_untrusted(hit.memory.content),
                         'category': hit.memory.category,
                         'people': hit.memory.people,
                         'happened_on': (hit.memory.happened_on.isoformat()
                                         if hit.memory.happened_on else None),
                         'score': round(hit.score, 4),
                         'matched_terms': hit.matched_terms,
                         'verification_status': hit.memory.provenance.verification_status,
                         'source': hit.memory.provenance.source}
                        for hit in hits],
            'count': len(hits),
        }
