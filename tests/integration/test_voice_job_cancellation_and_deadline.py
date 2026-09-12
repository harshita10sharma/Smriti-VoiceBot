"""Voice job cancellation, race safety against the worker, the processing
deadline, and the audio_expired status flag -- all genuinely new behavior
added on top of the existing bounded-queue/ownership/restart-recovery
machinery (already covered by test_voice_jobs.py and
test_voice_job_reliability.py, not repeated here).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from smriti_voice.api.app import create_app
from smriti_voice.voice_jobs import CANCELLED, COMPLETED, FAILED, PROCESSING, QUEUED


def _client(app, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEY', 'test-server-key')
    monkeypatch.setenv('SMRITI_AUTH_USER_ID', 'demo-user')
    return TestClient(create_app(app))


# --------------------------------------------------------------------------- #
# Repository-level cancellation semantics
# --------------------------------------------------------------------------- #
def test_cancel_a_queued_job(app):
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    assert app.voice_jobs.cancel(job_id) is True
    job = app.voice_jobs.get(job_id)
    assert job.status == CANCELLED
    assert job.error_code == 'CANCELLED_BY_CLIENT'


def test_cancel_a_processing_job(app):
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    assert app.voice_jobs.cancel(job_id) is True
    assert app.voice_jobs.get(job_id).status == CANCELLED


def test_cancelling_an_already_completed_job_is_a_no_op_not_an_error(app):
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    app.voice_jobs.mark_completed(job_id, audio_id='abc123', tts_provider='mock')

    assert app.voice_jobs.cancel(job_id) is False  # nothing to cancel
    job = app.voice_jobs.get(job_id)
    assert job.status == COMPLETED  # unchanged, not clobbered to cancelled


def test_cancelling_twice_only_the_first_call_reports_true(app):
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    assert app.voice_jobs.cancel(job_id) is True
    assert app.voice_jobs.cancel(job_id) is False


# --------------------------------------------------------------------------- #
# The race: cancel and a "still-in-flight" worker completion
# --------------------------------------------------------------------------- #
def test_cancel_then_late_worker_completion_does_not_resurrect_the_job(app):
    """Simulates the worker finishing synthesis *after* the client already
    cancelled: cancel() wins, and the worker's own mark_completed must be a
    silent no-op against the now-cancelled row, never overwriting it back
    to 'completed'."""
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    assert app.voice_jobs.cancel(job_id) is True

    # The worker, unaware, finishes late and tries to complete the job.
    applied = app.voice_jobs.mark_completed(job_id, audio_id='late-audio', tts_provider='mock')
    assert applied is False

    job = app.voice_jobs.get(job_id)
    assert job.status == CANCELLED  # still cancelled, never resurrected
    assert job.audio_id is None  # the late completion never wrote through


def test_worker_completion_then_late_cancel_attempt_does_not_undo_completion(app):
    """The opposite ordering: the worker completes first, a cancel request
    arrives after. The job stays completed -- cancelling something already
    done is a no-op, not a retroactive undo."""
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    app.voice_jobs.mark_completed(job_id, audio_id='real-audio', tts_provider='mock')

    assert app.voice_jobs.cancel(job_id) is False
    job = app.voice_jobs.get(job_id)
    assert job.status == COMPLETED
    assert job.audio_id == 'real-audio'


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
def test_cancel_endpoint_requires_ownership(app, monkeypatch, two_users):
    client = _client(app, monkeypatch)
    job_id = app.voice_jobs.create(user_id='other-user', session_id=None, language='eng',
                                   response_text='hi')
    resp = client.post(f'/v1/voice/jobs/{job_id}/cancel', headers={'x-api-key': 'test-server-key'})
    assert resp.status_code == 404  # not found-vs-unauthorized: identical, fail closed


def test_cancel_endpoint_happy_path(app, monkeypatch):
    client = _client(app, monkeypatch)
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    resp = client.post(f'/v1/voice/jobs/{job_id}/cancel', headers={'x-api-key': 'test-server-key'})
    assert resp.status_code == 200
    body = resp.json()
    assert body['cancelled'] is True
    assert body['status'] == 'cancelled'


def test_cancel_endpoint_unknown_job_id_is_404(app, monkeypatch):
    client = _client(app, monkeypatch)
    resp = client.post('/v1/voice/jobs/does-not-exist/cancel',
                       headers={'x-api-key': 'test-server-key'})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Processing deadline
# --------------------------------------------------------------------------- #
def test_a_job_stuck_processing_past_the_deadline_is_reported_failed(app):
    app.voice_jobs.processing_deadline_s = 1  # tight deadline for the test
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)

    # Force it to look like processing started well in the past.
    stale = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')
    with app.memory.repo.db.connect() as connection:
        connection.execute('UPDATE voice_jobs SET updated_at = ? WHERE job_id = ?',
                           (stale, job_id))

    job = app.voice_jobs.get(job_id)
    assert job.status == FAILED
    assert job.error_code == 'PROCESSING_TIMEOUT'


def test_deadline_enforcement_is_durable_not_just_reported(app):
    """The FAILED status from an overdue job must actually be written back,
    not merely synthesized on read -- a second, independent read sees the
    same terminal state."""
    app.voice_jobs.processing_deadline_s = 1
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    stale = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')
    with app.memory.repo.db.connect() as connection:
        connection.execute('UPDATE voice_jobs SET updated_at = ? WHERE job_id = ?',
                           (stale, job_id))

    app.voice_jobs.get(job_id)  # first read applies and writes the timeout

    with app.memory.repo.db.connect() as connection:
        row = connection.execute('SELECT status, error_code FROM voice_jobs WHERE job_id = ?',
                                 (job_id,)).fetchone()
    assert row['status'] == FAILED
    assert row['error_code'] == 'PROCESSING_TIMEOUT'


def test_a_job_within_the_deadline_is_unaffected(app):
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    job = app.voice_jobs.get(job_id)
    assert job.status == PROCESSING  # deadline (default 180s) not remotely reached


def test_queued_jobs_are_never_affected_by_the_processing_deadline(app):
    app.voice_jobs.processing_deadline_s = 1
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    stale = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')
    with app.memory.repo.db.connect() as connection:
        connection.execute('UPDATE voice_jobs SET updated_at = ? WHERE job_id = ?',
                           (stale, job_id))
    job = app.voice_jobs.get(job_id)
    assert job.status == QUEUED  # only 'processing' is subject to the deadline


# --------------------------------------------------------------------------- #
# audio_expired flag on the status endpoint
# --------------------------------------------------------------------------- #
def test_status_endpoint_reports_audio_expired_when_the_file_has_aged_out(app, monkeypatch, tmp_path):
    import os
    client = _client(app, monkeypatch)
    audio_id = app.tts.store.put(b'RIFF....WAVEfmt ' + b'\x00' * 20)
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    app.voice_jobs.mark_completed(job_id, audio_id=audio_id, tts_provider='mock')

    # Age the audio file past retention without a new write (prune() only
    # runs opportunistically on writes -- exactly the scenario path_for's
    # own read-time expiry check covers).
    path = app.tts.store.directory / f'{audio_id}.wav'
    old = datetime.now(timezone.utc).timestamp() - app.tts.store.retention_s - 5
    os.utime(path, (old, old))

    resp = client.get(f'/v1/voice/jobs/{job_id}', headers={'x-api-key': 'test-server-key'})
    body = resp.json()
    assert body['status'] == 'completed'  # the job itself did complete -- historically true
    assert body['audio_expired'] is True


def test_status_endpoint_does_not_report_expired_for_fresh_audio(app, monkeypatch):
    client = _client(app, monkeypatch)
    audio_id = app.tts.store.put(b'RIFF....WAVEfmt ' + b'\x00' * 20)
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    app.voice_jobs.mark_completed(job_id, audio_id=audio_id, tts_provider='mock')

    resp = client.get(f'/v1/voice/jobs/{job_id}', headers={'x-api-key': 'test-server-key'})
    assert resp.json()['audio_expired'] is False
