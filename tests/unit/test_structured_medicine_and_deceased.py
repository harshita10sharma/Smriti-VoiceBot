"""Structured medicine time/day matching (MemoryService.medicines) and
deceased-family-member handling (MemoryService._family_payload +
conversation/prompts.py). Both are additive to the existing free-text
medicine fields and existing family payload -- a medicine/family member
synced before these fields existed behaves exactly as before.
"""
from __future__ import annotations

from datetime import date

from smriti_voice.memory.models import FamilyMember, Medicine
from smriti_voice.memory.service import _format_minutes


def test_format_minutes():
    assert _format_minutes(480) == '08:00'
    assert _format_minutes(0) == '00:00'
    assert _format_minutes(1439) == '23:59'
    assert _format_minutes(None) is None


def test_medicine_with_no_days_of_week_is_never_filtered_by_day(app):
    app.memory.repo.add_medicine(Medicine(user_id='demo-user', name='LegacyMedicine',
                                          dosage='10mg', time_of_day='morning'))
    result = app.memory.medicines('demo-user', when='today')
    names = {m['name'] for m in result['medicines']}
    assert 'LegacyMedicine' in names


def test_medicine_scheduled_only_on_specific_weekday_is_filtered_by_day(app):
    # 2026-09-14 is a Monday (ISO weekday 1); 2026-09-15 is a Tuesday (2).
    monday = date(2026, 9, 14)
    tuesday = date(2026, 9, 15)
    app.memory.repo.add_medicine(Medicine(user_id='demo-user', name='MondayOnlyMedicine',
                                          chosen_time_min=480, days_of_week='1'))

    on_monday = app.memory.medicines('demo-user', when=monday.isoformat())
    on_tuesday = app.memory.medicines('demo-user', when=tuesday.isoformat())

    assert 'MondayOnlyMedicine' in {m['name'] for m in on_monday['medicines']}
    assert 'MondayOnlyMedicine' not in {m['name'] for m in on_tuesday['medicines']}


def test_medicine_time_fields_are_formatted_as_hh_mm(app):
    app.memory.repo.add_medicine(Medicine(user_id='demo-user', name='TimedMedicine',
                                          chosen_time_min=1230, window_start_min=1200,
                                          window_end_min=1260))
    result = app.memory.medicines('demo-user')
    medicine = next(m for m in result['medicines'] if m['name'] == 'TimedMedicine')
    assert medicine['chosen_time'] == '20:30'
    assert medicine['window_start'] == '20:00'
    assert medicine['window_end'] == '21:00'


def test_medicines_response_never_claims_adherence_information(app):
    """Structural guarantee: nothing in the returned per-medicine payload
    shape can express whether a dose was taken -- there is no such key to
    accidentally populate."""
    app.memory.repo.add_medicine(Medicine(user_id='demo-user', name='AdherenceCheckMedicine'))
    result = app.memory.medicines('demo-user')
    medicine = next(m for m in result['medicines'] if m['name'] == 'AdherenceCheckMedicine')
    for forbidden in ('taken', 'adherence', 'missed_dose', 'confirmed_taken'):
        assert forbidden not in medicine


# --------------------------------------------------------------------------- #
# Deceased family members
# --------------------------------------------------------------------------- #
def test_deceased_flag_is_passed_through_family_payload(app):
    app.memory.repo.add_family_member(FamilyMember(
        user_id='demo-user', name='Late Grandfather', relation='grandfather',
        is_deceased=True, memory_prompt='Loved telling stories about the war.'))
    result = app.memory.find_family('demo-user', name='Late Grandfather')
    assert result[0]['is_deceased'] is True
    assert 'stories' in result[0]['memory_prompt']


def test_living_family_member_defaults_to_not_deceased(app):
    app.memory.repo.add_family_member(FamilyMember(
        user_id='demo-user', name='Living Cousin', relation='cousin'))
    result = app.memory.find_family('demo-user', name='Living Cousin')
    assert result[0]['is_deceased'] is False


def test_system_prompt_instructs_the_model_about_deceased_family_members():
    from smriti_voice.conversation.prompts import build_system_prompt
    prompt = build_system_prompt('eng')
    assert 'deceased' in prompt.lower()
    assert 'never guess' in prompt.lower() or 'never speak' in prompt.lower()
