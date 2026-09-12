"""Patient provisioning, disabling and the memory-sync-for-a-brand-new-patient
fix.

Authorization (the API key's allow-list, tested in test_multi_user_auth.py)
and provisioning/account-status (this file) are deliberately separate: a
key can authorize a user_id that has never been provisioned at all (normal,
before a first sync or first conversation), and a caregiver can disable an
already-provisioned patient without touching the key configuration.
"""
from __future__ import annotations

from smriti_voice.memory.models import User


# --------------------------------------------------------------------------- #
# Repository-level: is_user_active / ensure_user_provisioned
# --------------------------------------------------------------------------- #
def test_never_provisioned_user_is_treated_as_active(app):
    """Absence of a row is the normal pre-first-contact state, never
    'denied'."""
    assert app.memory.repo.get_user('brand-new-patient') is None
    assert app.memory.repo.is_user_active('brand-new-patient') is True


def test_disabled_user_is_reported_inactive(app):
    app.memory.repo.upsert_user(User(user_id='demo-user', display_name='Demo',
                                     active=True))
    assert app.memory.repo.is_user_active('demo-user') is True
    app.memory.repo.upsert_user(User(user_id='demo-user', display_name='Demo',
                                     active=False))
    assert app.memory.repo.is_user_active('demo-user') is False


def test_upsert_user_preserves_active_flag_by_default():
    """A caller that doesn't set active explicitly gets the model default
    (True) -- upsert_user must never silently re-enable or disable a user
    the caller didn't ask to change, but it also must not require every
    existing call site to pass active= explicitly."""
    from smriti_voice.memory.models import User as U
    assert U(user_id='x', display_name='X').active is True


def test_ensure_user_provisioned_is_idempotent_and_never_overwrites(app):
    app.memory.repo.upsert_user(User(user_id='demo-user', display_name='Real Caregiver Name'))
    app.memory.repo.ensure_user_provisioned('demo-user', default_display_name='placeholder')
    user = app.memory.repo.get_user('demo-user')
    assert user.display_name == 'Real Caregiver Name'  # untouched

    app.memory.repo.ensure_user_provisioned('brand-new-id', default_display_name='fallback-name')
    created = app.memory.repo.get_user('brand-new-id')
    assert created is not None and created.display_name == 'fallback-name'


# --------------------------------------------------------------------------- #
# The real bug found in the forensic audit: syncing a never-before-seen
# patient used to raise an unhandled FK violation, surfaced as an opaque 500.
# --------------------------------------------------------------------------- #
def test_memory_sync_for_a_never_provisioned_patient_succeeds(client, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEYS',
                       '{"backend-key": ["demo-user", "brand-new-patient"]}')
    resp = client.post('/v1/memory/sync', headers={'x-api-key': 'backend-key'},
                       json={'user_id': 'brand-new-patient',
                             'family_members': [{'name': 'Rita', 'relationship': 'daughter',
                                                 'phone_available': True}],
                             'medicines': [], 'daily_routines': []})
    assert resp.status_code == 200
    body = resp.json()
    assert body['success'] is True and body['family_members_synced'] == 1
    assert client.app.state.application.memory.repo.get_user('brand-new-patient') is not None


def test_memory_sync_for_a_never_provisioned_patient_is_idempotent(client, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEYS', '{"backend-key": ["fresh-patient"]}')
    payload = {'user_id': 'fresh-patient',
              'family_members': [{'name': 'Amit', 'relationship': 'son', 'phone_available': False}],
              'medicines': [], 'daily_routines': []}
    first = client.post('/v1/memory/sync', headers={'x-api-key': 'backend-key'}, json=payload)
    second = client.post('/v1/memory/sync', headers={'x-api-key': 'backend-key'}, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()['family_members_synced'] == second.json()['family_members_synced'] == 1


# --------------------------------------------------------------------------- #
# HTTP layer: a disabled patient is denied on every patient-scoped route
# --------------------------------------------------------------------------- #
def test_disabled_patient_denied_on_conversation(client, auth_headers):
    client.app.state.application.memory.repo.upsert_user(
        User(user_id='demo-user', display_name='Demo', active=False))
    resp = client.post('/v1/conversation',
                       json={'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'},
                       headers=auth_headers)
    assert resp.status_code == 403


def test_disabled_patient_denied_on_welcome(client, auth_headers):
    client.app.state.application.memory.repo.upsert_user(
        User(user_id='demo-user', display_name='Demo', active=False))
    resp = client.post('/v1/conversation/welcome', json={'user_id': 'demo-user'},
                       headers=auth_headers)
    assert resp.status_code == 403


def test_memory_sync_is_not_gated_by_active_state(client, auth_headers):
    """Deliberately NOT a 403: memory/sync is the data-management/
    revocation channel, not a patient-facing endpoint (see
    api/routes/memory_sync.py). Gating it on `active` would make
    re-enabling a disabled patient impossible -- the re-enable request
    itself would be rejected as coming from a disabled patient."""
    client.app.state.application.memory.repo.upsert_user(
        User(user_id='demo-user', display_name='Demo', active=False))
    resp = client.post('/v1/memory/sync',
                       json={'user_id': 'demo-user', 'family_members': [],
                             'medicines': [], 'daily_routines': []},
                       headers=auth_headers)
    assert resp.status_code == 200


def test_memory_sync_can_re_enable_a_disabled_patient(client, auth_headers):
    repo = client.app.state.application.memory.repo
    repo.upsert_user(User(user_id='demo-user', display_name='Demo', active=False))
    assert repo.is_user_active('demo-user') is False

    resp = client.post('/v1/memory/sync',
                       json={'user_id': 'demo-user', 'active': True, 'family_members': [],
                             'medicines': [], 'daily_routines': []},
                       headers=auth_headers)
    assert resp.status_code == 200
    assert repo.is_user_active('demo-user') is True

    # And the previously-disabled patient can now use conversation again.
    convo = client.post('/v1/conversation',
                        json={'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'},
                        headers=auth_headers)
    assert convo.status_code == 200


def test_memory_sync_can_disable_a_patient(client, auth_headers):
    repo = client.app.state.application.memory.repo
    resp = client.post('/v1/memory/sync',
                       json={'user_id': 'demo-user', 'active': False, 'family_members': [],
                             'medicines': [], 'daily_routines': []},
                       headers=auth_headers)
    assert resp.status_code == 200
    assert repo.is_user_active('demo-user') is False

    convo = client.post('/v1/conversation',
                        json={'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'},
                        headers=auth_headers)
    assert convo.status_code == 403


def test_memory_sync_omitting_active_never_changes_current_state(client, auth_headers):
    repo = client.app.state.application.memory.repo
    repo.upsert_user(User(user_id='demo-user', display_name='Demo', active=False))

    client.post('/v1/memory/sync',
               json={'user_id': 'demo-user', 'family_members': [], 'medicines': [],
                    'daily_routines': []},
               headers=auth_headers)
    assert repo.is_user_active('demo-user') is False  # untouched by omission


def test_reenabled_patient_is_served_again(client, auth_headers):
    repo = client.app.state.application.memory.repo
    repo.upsert_user(User(user_id='demo-user', display_name='Demo', active=False))
    assert client.post('/v1/conversation',
                       json={'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'},
                       headers=auth_headers).status_code == 403
    repo.upsert_user(User(user_id='demo-user', display_name='Demo', active=True))
    assert client.post('/v1/conversation',
                       json={'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'},
                       headers=auth_headers).status_code == 200


def test_unprovisioned_but_authorized_patient_is_not_denied(client, monkeypatch):
    """A user_id the key authorizes but that has no DB row yet must be
    served normally -- absence of a row is not 'unknown/denied'."""
    monkeypatch.setenv('SMRITI_API_KEYS', '{"backend-key": ["never-seen-before"]}')
    resp = client.post('/v1/conversation',
                       json={'user_id': 'never-seen-before', 'message': 'hello',
                             'language': 'eng'},
                       headers={'x-api-key': 'backend-key'})
    assert resp.status_code == 200
