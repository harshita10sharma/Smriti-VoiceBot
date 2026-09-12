"""POST /v1/memory/sync — the caregiver full-replace memory sync endpoint.

Uses the existing memory architecture end to end (real MemoryRepository,
real SQLite, real provenance system) — nothing here is a parallel model or a
mock database. `app`/`auth_headers`/`two_users` come from tests/conftest.py.
"""
from __future__ import annotations

import json

import pytest


def sync(client, headers, **overrides):
    payload = {
        'user_id': 'demo-user',
        'family_members': [{'name': 'Bina', 'relationship': 'daughter', 'phone_available': True}],
        'medicines': [{'name': 'Metformin', 'dose': '500mg', 'schedule': 'morning, after food'}],
        'daily_routines': [{'time': '08:00', 'activity': 'breakfast'}],
    }
    payload.update(overrides)
    return client.post('/v1/memory/sync', headers=headers, json=payload)


# --------------------------------------------------------------------------- #
# Successful sync, per-table, and the response shape
# --------------------------------------------------------------------------- #
def test_successful_sync_reports_counts(client, auth_headers):
    response = sync(client, auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        'success': True, 'user_id': 'demo-user',
        'family_members_synced': 1, 'medicines_synced': 1, 'daily_routines_synced': 1,
        # Additive fields for the opt-in revisioning contract (unused here,
        # since this request omits source_revision entirely).
        'status': 'applied', 'source_revision': None,
    }


def test_family_member_is_actually_stored(client, auth_headers, app):
    sync(client, auth_headers)
    synced = [f for f in app.memory.repo.list_family('demo-user')
             if f.provenance.created_by == 'memory_sync']
    assert len(synced) == 1
    assert synced[0].name == 'Bina' and synced[0].relation == 'daughter'


def test_medicine_is_actually_stored(client, auth_headers, app):
    sync(client, auth_headers)
    medicines = app.memory.repo.list_medicines('demo-user')
    synced = [m for m in medicines if m.provenance.created_by == 'memory_sync']
    assert len(synced) == 1
    assert synced[0].name == 'Metformin' and synced[0].dosage == '500mg'
    assert synced[0].instructions == 'morning, after food'


def test_routine_is_actually_stored(client, auth_headers, app):
    sync(client, auth_headers)
    routines = app.memory.repo.list_routine('demo-user')
    synced = [r for r in routines if r.provenance.created_by == 'memory_sync']
    assert len(synced) == 1
    assert synced[0].title == 'breakfast' and synced[0].routine_time == '08:00'


# --------------------------------------------------------------------------- #
# Full replacement semantics
# --------------------------------------------------------------------------- #
def test_second_sync_fully_replaces_the_caregiver_dataset(client, auth_headers, app):
    sync(client, auth_headers)
    sync(client, auth_headers, family_members=[
        {'name': 'Rakesh', 'relationship': 'son', 'phone_available': False}])
    family = [f for f in app.memory.repo.list_family('demo-user')
             if f.provenance.created_by == 'memory_sync']
    assert [f.name for f in family] == ['Rakesh']


def test_empty_arrays_clear_the_caregiver_dataset(client, auth_headers, app):
    sync(client, auth_headers)
    response = sync(client, auth_headers, family_members=[], medicines=[], daily_routines=[])
    assert response.status_code == 200
    assert response.json() == {
        'success': True, 'user_id': 'demo-user',
        'family_members_synced': 0, 'medicines_synced': 0, 'daily_routines_synced': 0,
        'status': 'applied', 'source_revision': None,
    }
    assert [f for f in app.memory.repo.list_family('demo-user')
           if f.provenance.created_by == 'memory_sync'] == []
    assert [m for m in app.memory.repo.list_medicines('demo-user')
           if m.provenance.created_by == 'memory_sync'] == []
    assert [r for r in app.memory.repo.list_routine('demo-user')
           if r.provenance.created_by == 'memory_sync'] == []


def test_repeated_identical_sync_is_idempotent(client, auth_headers, app):
    for _ in range(3):
        response = sync(client, auth_headers)
        assert response.status_code == 200
    family = [f for f in app.memory.repo.list_family('demo-user')
             if f.provenance.created_by == 'memory_sync']
    medicines = [m for m in app.memory.repo.list_medicines('demo-user')
                if m.provenance.created_by == 'memory_sync']
    routines = [r for r in app.memory.repo.list_routine('demo-user')
               if r.provenance.created_by == 'memory_sync']
    assert len(family) == 1 and len(medicines) == 1 and len(routines) == 1


def test_preserves_non_caregiver_records(client, auth_headers, app):
    """Seed data (source='seed') must survive a sync untouched, including a
    repeat sync and an empty-array clear."""
    before = {f.name for f in app.memory.repo.list_family('demo-user')}
    assert 'Meera' in before and 'Rakesh' in before  # from seed_demo_user

    sync(client, auth_headers)
    sync(client, auth_headers, family_members=[], medicines=[], daily_routines=[])

    after = app.memory.repo.list_family('demo-user')
    seed_rows = [f for f in after if f.provenance.source == 'seed']
    assert {f.name for f in seed_rows} == before
    for row in seed_rows:
        assert row.provenance.created_by == 'seed-script'


# --------------------------------------------------------------------------- #
# Rollback / transactional safety
# --------------------------------------------------------------------------- #
def test_sync_rolls_back_atomically_on_failure(app):
    """If any part of the sync fails, none of it is applied — the previous
    caregiver-synced state (here: one family member from a prior successful
    sync) must be exactly what remains.

    The failure is a real one: `daily_routines.title` is NOT NULL at the SQL
    level (database/migrations.py), and `DailyRoutine.model_construct` skips
    Pydantic validation, so a None title reaches sqlite3 and raises
    IntegrityError for real — no mocking of sqlite3 itself required.
    """
    import sqlite3

    from smriti_voice.memory.models import DailyRoutine, FamilyMember, Provenance
    provenance = Provenance(source='caregiver', created_by='memory_sync',
                            verification_status='verified')
    app.memory.repo.sync_caregiver_memory(
        'demo-user',
        family_members=[FamilyMember(user_id='demo-user', name='Original', relation='son',
                                     provenance=provenance)],
        medicines=[], routines=[])

    invalid_routine = DailyRoutine.model_construct(
        user_id='demo-user', title=None, routine_time=None, day_of_week=None, notes=None,
        provenance=provenance)
    with pytest.raises(sqlite3.IntegrityError):
        app.memory.repo.sync_caregiver_memory(
            'demo-user',
            family_members=[FamilyMember(user_id='demo-user', name='New', relation='daughter',
                                         provenance=provenance)],
            medicines=[], routines=[invalid_routine])

    family = [f for f in app.memory.repo.list_family('demo-user')
             if f.provenance.created_by == 'memory_sync']
    assert [f.name for f in family] == ['Original'], (
        'a failed sync must not delete or partially replace the previous caregiver state')


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_synced_rows_use_caregiver_source_and_verified_status(client, auth_headers, app):
    sync(client, auth_headers)
    family = [f for f in app.memory.repo.list_family('demo-user')
             if f.provenance.created_by == 'memory_sync'][0]
    assert family.provenance.source == 'caregiver'
    assert family.provenance.verification_status == 'verified'
    medicine = [m for m in app.memory.repo.list_medicines('demo-user')
               if m.provenance.created_by == 'memory_sync'][0]
    assert medicine.provenance.source == 'caregiver'
    assert medicine.provenance.verification_status == 'verified'


# --------------------------------------------------------------------------- #
# Phone privacy
# --------------------------------------------------------------------------- #
def test_phone_available_is_never_persisted_as_a_real_number(client, auth_headers, app):
    sync(client, auth_headers, family_members=[
        {'name': 'Bina', 'relationship': 'daughter', 'phone_available': True}])
    member = [f for f in app.memory.repo.list_family('demo-user')
             if f.provenance.created_by == 'memory_sync'][0]
    assert member.phone is None


@pytest.mark.parametrize('bad_field', ['phone', 'phone_number', 'mobile', 'contact_number',
                                       'telephone'])
def test_raw_phone_fields_are_rejected(client, auth_headers, bad_field):
    response = sync(client, auth_headers, family_members=[
        {'name': 'Bina', 'relationship': 'daughter', 'phone_available': True,
         bad_field: '+919000000000'}])
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_unknown_top_level_field_is_rejected(client, auth_headers):
    response = sync(client, auth_headers, extra_field='not allowed')
    assert response.status_code == 422


def test_missing_required_field_is_rejected(client, auth_headers):
    response = client.post('/v1/memory/sync', headers=auth_headers,
                           json={'user_id': 'demo-user',
                                 'family_members': [{'relationship': 'daughter'}]})
    assert response.status_code == 422


def test_invalid_time_format_is_rejected(client, auth_headers):
    for bad_time in ('8am', '25:00', '08:60', 'morning', '8:00'):
        response = sync(client, auth_headers,
                        daily_routines=[{'time': bad_time, 'activity': 'breakfast'}])
        assert response.status_code == 422, bad_time


def test_invalid_user_id_is_rejected(client, auth_headers):
    response = sync(client, auth_headers, user_id='bad id; drop table')
    assert response.status_code == 422


def test_malformed_json_is_rejected(client, auth_headers):
    response = client.post('/v1/memory/sync', headers=auth_headers, content=b'not json{')
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Authentication / authorization / user isolation
# --------------------------------------------------------------------------- #
def test_missing_key_is_rejected(client):
    response = client.post('/v1/memory/sync', json={
        'user_id': 'demo-user', 'family_members': [], 'medicines': [], 'daily_routines': []})
    assert response.status_code == 401


def test_wrong_key_is_rejected(client):
    response = client.post('/v1/memory/sync', headers={'x-api-key': 'wrong'}, json={
        'user_id': 'demo-user', 'family_members': [], 'medicines': [], 'daily_routines': []})
    assert response.status_code == 401


def test_authenticated_key_cannot_sync_an_unauthorized_user(client, auth_headers):
    """The single-user deployment's key is bound to demo-user; it must not
    be usable to sync a different patient's data."""
    response = sync(client, auth_headers, user_id='other-user')
    assert response.status_code == 403


def test_backend_key_can_only_sync_its_authorized_patients(two_users, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('SMRITI_API_KEY', raising=False)
    monkeypatch.delenv('SMRITI_AUTH_USER_ID', raising=False)
    monkeypatch.setenv('SMRITI_API_KEYS', json.dumps({
        'backend-key': ['demo-user', 'other-user'],
    }))
    client = TestClient(create_app(two_users))

    ok = sync(client, {'x-api-key': 'backend-key'}, user_id='other-user')
    assert ok.status_code == 200

    forbidden = sync(client, {'x-api-key': 'backend-key'}, user_id='elder-999')
    assert forbidden.status_code == 403


def test_sync_never_touches_a_different_users_data(client, auth_headers, two_users):
    """A sync for demo-user must never modify other-user's rows, even though
    both exist in the same database."""
    other_family_before = two_users.memory.repo.list_family('other-user')
    sync(client, auth_headers)  # syncs demo-user only
    other_family_after = two_users.memory.repo.list_family('other-user')
    assert other_family_before == other_family_after
