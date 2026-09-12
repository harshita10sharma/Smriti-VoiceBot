"""Pydantic input schemas for every tool.

``extra='forbid'`` on every model is deliberate: an LLM that invents an extra
argument gets a validation error rather than a silently ignored field.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class EmptyArgs(StrictModel):
    pass


class FamilyMemberQuery(StrictModel):
    name: str | None = Field(default=None, max_length=80,
                             description="The person's name, e.g. 'Bina'.")
    relation: str | None = Field(default=None, max_length=40,
                                 description="Relationship, e.g. 'daughter', 'son'.")


class MemorySearchQuery(StrictModel):
    query: str = Field(max_length=300, description='What the user is asking about.')
    limit: int = Field(default=3, ge=1, le=5)


class DateQuery(StrictModel):
    when: str = Field(default='today', max_length=40,
                      description="'today', 'yesterday', 'tomorrow' or an ISO date (YYYY-MM-DD).")


class AppointmentQuery(DateQuery):
    days: int = Field(default=0, ge=0, le=30,
                      description='Look ahead this many days from the date. 0 = that day only.')


class MedicationQuery(StrictModel):
    time_of_day: str | None = Field(default=None, max_length=20,
                                    description="'morning', 'afternoon', 'evening' or 'night'.")
    when: str | None = Field(default=None, max_length=40,
                             description="'today', 'tomorrow' or an ISO date (YYYY-MM-DD). "
                                         "Omit for no day filtering.")


class WeatherQuery(StrictModel):
    location: str | None = Field(default=None, max_length=80,
                                 description="City name. Defaults to the user's saved location.")


class OpenAppArgs(StrictModel):
    app: str = Field(max_length=40, description='One of the SMRITI screens the user may open.')


class StartGameArgs(StrictModel):
    game: str | None = Field(default=None, max_length=40,
                             description='Game key, or omit to open the games screen.')


class CallFamilyArgs(StrictModel):
    name: str = Field(max_length=80, description='Name of a saved, trusted family contact.')


class CreateReminderArgs(StrictModel):
    text: str = Field(min_length=1, max_length=200, description='What to be reminded about.')
    remind_at: str | None = Field(default=None, max_length=40,
                                  description="Time such as '18:00', or a day and time.")


class PhoneHelpArgs(StrictModel):
    topic: str = Field(max_length=80,
                       description="What the user wants to do, e.g. 'open whatsapp'.")
