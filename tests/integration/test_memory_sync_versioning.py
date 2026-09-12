"""Opt-in memory-sync revisioning (source_revision/schema_version) and
stable external IDs.

A request that omits source_revision entirely gets the exact original,
unversioned full-replace behaviour -- this file's first test group proves
that explicitly. A request that includes it gets stale/no-op/conflict/apply
semantics, backed by the memory_sync_state table.
"""
from __future__ import annotations


def sync(client, auth_headers, **overrides):
    body = {'user_id': 'demo-user', 'family_members': [], 'medicines': [], 'daily_routines': []}
    body.update(overrides)
    return client.post('/v1/memory/sync', json=body, headers=auth_headers)


# --------------------------------------------------------------------------- #
# Backward compatibility: omitting source_revision changes nothing
# --------------------------------------------------------------------------- #
def test_omitting_source_revision_always_applies_like_before(client, auth_headers):
    first = sync(client, auth_headers,
                family_members=[{'name': 'A', 'relationship': 'son', 'phone_available': False}])
    second = sync(client, auth_headers,
                 family_members=[{'name': 'B', 'relationship': 'daughter', 'phone_available': False}])
    assert first.status_code == second.status_code == 200
    assert first.json()['status'] == second.json()['status'] == 'applied'
    assert first.json()['source_revision'] is None
    assert second.json()['source_revision'] is None


# --------------------------------------------------------------------------- #
# Revisioned sync: apply / no-op / conflict / stale
# --------------------------------------------------------------------------- #
def test_first_versioned_sync_applies(client, auth_headers):
    resp = sync(client, auth_headers, source_revision=1,
               family_members=[{'name': 'Bina', 'relationship': 'daughter',
                                'phone_available': True}])
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'applied'
    assert body['source_revision'] == 1
    assert body['family_members_synced'] == 1


def test_identical_revision_and_content_is_a_harmless_no_op(client, auth_headers):
    payload = {'source_revision': 1,
              'family_members': [{'name': 'Bina', 'relationship': 'daughter',
                                  'phone_available': True}]}
    first = sync(client, auth_headers, **payload)
    second = sync(client, auth_headers, **payload)
    assert first.status_code == second.status_code == 200
    assert second.json()['status'] == 'no_op'
    assert second.json()['source_revision'] == 1


def test_same_revision_different_content_is_a_conflict(client, auth_headers):
    sync(client, auth_headers, source_revision=1,
        family_members=[{'name': 'Bina', 'relationship': 'daughter', 'phone_available': True}])
    conflicting = sync(client, auth_headers, source_revision=1,
                       family_members=[{'name': 'Rita', 'relationship': 'daughter',
                                        'phone_available': True}])
    assert conflicting.status_code == 409


def test_older_revision_is_rejected_as_stale(client, auth_headers):
    sync(client, auth_headers, source_revision=5,
        family_members=[{'name': 'Bina', 'relationship': 'daughter', 'phone_available': True}])
    stale = sync(client, auth_headers, source_revision=3,
                family_members=[{'name': 'Someone', 'relationship': 'son',
                                 'phone_available': False}])
    assert stale.status_code == 409


def test_newer_revision_applies_and_replaces_content(client, auth_headers):
    sync(client, auth_headers, source_revision=1,
        family_members=[{'name': 'Bina', 'relationship': 'daughter', 'phone_available': True}])
    newer = sync(client, auth_headers, source_revision=2,
                family_members=[{'name': 'Rita', 'relationship': 'daughter',
                                 'phone_available': True}])
    assert newer.status_code == 200
    body = newer.json()
    assert body['status'] == 'applied'
    assert body['source_revision'] == 2
    assert body['family_members_synced'] == 1


def test_a_rejected_stale_or_conflicting_sync_does_not_change_stored_data(client, auth_headers, app):
    sync(client, auth_headers, source_revision=5,
        family_members=[{'name': 'Bina', 'relationship': 'daughter', 'phone_available': True}])
    conflicting = sync(client, auth_headers, source_revision=5,
                       family_members=[{'name': 'DifferentName', 'relationship': 'daughter',
                                        'phone_available': True}])
    assert conflicting.status_code == 409
    names = {m.name for m in app.memory.repo.list_family('demo-user')}
    assert 'Bina' in names  # the applied revision is untouched
    assert 'DifferentName' not in names  # the rejected conflicting sync never wrote anything


def test_concurrent_identical_revisioned_syncs_do_not_duplicate_state(client, auth_headers, app):
    """Not a true concurrency test (TestClient is synchronous), but proves
    the state table itself never accumulates more than one row per patient
    across repeated calls with the same revision."""
    payload = {'source_revision': 7,
              'family_members': [{'name': 'X', 'relationship': 'son', 'phone_available': False}]}
    for _ in range(3):
        sync(client, auth_headers, **payload)
    with app.memory.repo.db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM memory_sync_state WHERE user_id = 'demo-user'").fetchone()[0]
    assert count == 1


# --------------------------------------------------------------------------- #
# External IDs and patient context fields round-trip
# --------------------------------------------------------------------------- #
def test_external_ids_round_trip_through_sync(client, auth_headers, app):
    # Deliberately distinct from any seeded demo-user name/medicine/routine
    # so lookups below are unambiguous.
    resp = sync(client, auth_headers,
               family_members=[{'name': 'Zubeidi Khanam', 'relationship': 'daughter',
                                'phone_available': True, 'external_id': 'person-uuid-1'}],
               medicines=[{'name': 'ExternalIdTestMedicine', 'dose': '500mg',
                          'external_id': 'med-uuid-1', 'chosen_time_min': 480,
                          'window_start_min': 420, 'window_end_min': 540,
                          'days_of_week': '1,2,3,4,5,6,7'}],
               daily_routines=[{'activity': 'ExternalIdTestRoutine', 'time': '08:00',
                                'external_id': 'routine-uuid-1'}])
    assert resp.status_code == 200

    member = app.memory.repo.find_family('demo-user', name='Zubeidi Khanam')[0]
    assert member.external_id == 'person-uuid-1'

    medicine = next(m for m in app.memory.repo.list_medicines('demo-user')
                    if m.name == 'ExternalIdTestMedicine')
    assert medicine.external_id == 'med-uuid-1'
    assert medicine.chosen_time_min == 480
    assert medicine.days_of_week == '1,2,3,4,5,6,7'

    routine = next(r for r in app.memory.repo.list_routine('demo-user')
                   if r.title == 'ExternalIdTestRoutine')
    assert routine.external_id == 'routine-uuid-1'


def test_patient_context_fields_are_stored_and_preserved_on_omission(client, auth_headers, app):
    sync(client, auth_headers, display_name='Ibemhal Devi', external_id='patient-uuid-1',
        timezone='Asia/Kolkata', language_code='asm')
    user = app.memory.repo.get_user('demo-user')
    assert user.display_name == 'Ibemhal Devi'
    assert user.external_id == 'patient-uuid-1'
    assert user.timezone == 'Asia/Kolkata'
    assert user.preferred_language == 'asm'

    # A later sync that omits these fields must not blank them out.
    sync(client, auth_headers)
    user_after = app.memory.repo.get_user('demo-user')
    assert user_after.display_name == 'Ibemhal Devi'
    assert user_after.external_id == 'patient-uuid-1'
    assert user_after.timezone == 'Asia/Kolkata'


def test_get_user_by_external_id_resolves_the_patient(client, auth_headers, app):
    sync(client, auth_headers, external_id='patient-uuid-2')
    user = app.memory.repo.get_user_by_external_id('patient-uuid-2')
    assert user is not None and user.user_id == 'demo-user'
    assert app.memory.repo.get_user_by_external_id('no-such-uuid') is None


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #
def test_invalid_days_of_week_is_rejected(client, auth_headers):
    resp = sync(client, auth_headers,
               medicines=[{'name': 'X', 'days_of_week': '1,8,3'}])
    assert resp.status_code == 422


def test_wrapping_medicine_window_is_rejected(client, auth_headers):
    resp = sync(client, auth_headers,
               medicines=[{'name': 'X', 'window_start_min': 600, 'window_end_min': 300}])
    assert resp.status_code == 422


def test_out_of_range_chosen_time_min_is_rejected(client, auth_headers):
    resp = sync(client, auth_headers, medicines=[{'name': 'X', 'chosen_time_min': 1500}])
    assert resp.status_code == 422
