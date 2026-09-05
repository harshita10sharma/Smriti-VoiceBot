"""Personal-memory domain models.

Every model carries provenance.  ``verification_status`` is what stops an
AI-generated sentence from silently becoming a trusted fact about a person's
family or medication: anything the assistant writes is ``unverified`` until a
caregiver confirms it, and the read tools pass that flag through to the model.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal['caregiver', 'user', 'assistant', 'import', 'seed']
Verification = Literal['verified', 'unverified', 'rejected']


class Provenance(BaseModel):
    source: Source = 'caregiver'
    created_by: str | None = None
    confidence: float = 1.0
    verification_status: Verification = 'verified'
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_trusted(self) -> bool:
        return self.verification_status == 'verified'


class User(BaseModel):
    user_id: str
    display_name: str
    preferred_language: str = 'eng'
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class FamilyMember(BaseModel):
    id: int | None = None
    user_id: str
    name: str
    relation: str
    phone: str | None = None
    is_trusted_contact: bool = False
    is_primary_contact: bool = False
    lives_in: str | None = None
    notes: str | None = None
    photo_path: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class PersonalMemory(BaseModel):
    id: int | None = None
    user_id: str
    title: str
    content: str
    category: str = 'general'
    people: str | None = None
    happened_on: date | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class Meal(BaseModel):
    id: int | None = None
    user_id: str
    meal_date: date
    meal_type: str
    description: str
    provenance: Provenance = Field(default_factory=Provenance)


class Medicine(BaseModel):
    id: int | None = None
    user_id: str
    name: str
    dosage: str | None = None
    schedule_time: str | None = None
    time_of_day: str | None = None
    instructions: str | None = None
    active: bool = True
    prescribed_by: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class Appointment(BaseModel):
    id: int | None = None
    user_id: str
    title: str
    appointment_date: date
    appointment_time: str | None = None
    location: str | None = None
    with_person: str | None = None
    notes: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class DailyRoutine(BaseModel):
    id: int | None = None
    user_id: str
    title: str
    routine_time: str | None = None
    day_of_week: str | None = None
    notes: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class Visitor(BaseModel):
    id: int | None = None
    user_id: str
    name: str
    relation: str | None = None
    visit_date: date
    visit_time: str | None = None
    notes: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)


class Reminder(BaseModel):
    id: int | None = None
    user_id: str
    text: str
    remind_at: str | None = None
    recurrence: str | None = None
    status: str = 'active'
    provenance: Provenance = Field(default_factory=Provenance)


class Game(BaseModel):
    id: int | None = None
    user_id: str
    game_key: str
    display_name: str
    description: str | None = None
    enabled: bool = True
