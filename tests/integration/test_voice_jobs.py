"""The async voice-job endpoints: creation, polling, and the guarantees the
worker makes (one execution per job, real audio before 'completed', correct
language preserved end to end).
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from smriti_voice.api.app import create_app
from smriti_voice.voice_jobs import COMPLETED, FAILED, PROCESSING, QUEUED


@pytest.fixture
def jobs_client(app, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEY', 'test-server-key')
    monkeypatch.setenv('SMRITI_AUTH_USER_ID', 'demo-user')
    return TestClient(create_app(app)), app


def wait_for_status(app, job_id, *, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = app.voice_jobs.get(job_id)
        if job and job.status in (COMPLETED, FAILED):
            return job
        time.sleep(0.01)
    raise AssertionError(f'job {job_id} never reached a terminal state')


# --------------------------------------------------------------------------- #
# Creation, queued/processing/completed/failed states
# --------------------------------------------------------------------------- #
def test_job_starts_queued_then_completes(jobs_client):
    client, app = jobs_client
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None,
                                   language='eng', response_text='hello there')
    # Not submitted yet: still queued.
    assert app.voice_jobs.get(job_id).status == QUEUED

    app.voice_job_worker.submit(job_id)
    job = wait_for_status(app, job_id)
    assert job.status == COMPLETED
    assert job.audio_id


def test_job_reports_processing_while_synthesis_is_in_flight(jobs_client):
    client, app = jobs_client
    release = threading.Event()
    real_synthesize = app.tts.synthesize

    def slow_synthesize(text, language, **kwargs):
        release.wait(timeout=2.0)
        return real_synthesize(text, language, **kwargs)

    app.tts.synthesize = slow_synthesize
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None,
                                   language='eng', response_text='slow one')
    app.voice_job_worker.submit(job_id)

    deadline = time.monotonic() + 2.0
    seen_processing = False
    while time.monotonic() < deadline:
        if app.voice_jobs.get(job_id).status == PROCESSING:
            seen_processing = True
            break
        time.sleep(0.005)
    release.set()
    assert seen_processing, 'job never observably entered the processing state'
    job = wait_for_status(app, job_id)
    assert job.status == COMPLETED


def test_job_fails_cleanly_when_no_provider_supports_the_language(jobs_client, monkeypatch):
    client, app = jobs_client
    monkeypatch.setenv('SMRITI_TTS_PROVIDER', 'auto')
    monkeypatch.setenv('SARVAM_API_KEY', 'present-but-never-called')
    monkeypatch.setenv('SMRITI_INDIC_PARLER_ENABLED', '0')
    from smriti_voice.config import AppConfig
    from smriti_voice.tts.router import TTSRouter
    app.tts = TTSRouter(AppConfig.load(), app.languages)

    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None,
                                   language='asm', response_text='no voice for this one')
    app.voice_job_worker.submit(job_id)
    job = wait_for_status(app, job_id)
    assert job.status == FAILED
    assert job.error_code == 'NO_TTS_PROVIDER_SUPPORTS_LANGUAGE'
    assert job.audio_id is None


def test_missing_job_is_reported_as_not_found(jobs_client, auth_headers):
    client, _ = jobs_client
    response = client.get('/v1/voice/jobs/' + 'a' * 32, headers=auth_headers)
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# No duplicate TTS execution
# --------------------------------------------------------------------------- #
def test_submitting_the_same_job_twice_synthesises_only_once(jobs_client):
    client, app = jobs_client
    calls = []
    real_synthesize = app.tts.synthesize

    def counting_synthesize(text, language, **kwargs):
        calls.append(1)
        return real_synthesize(text, language, **kwargs)

    app.tts.synthesize = counting_synthesize
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None,
                                   language='eng', response_text='only once please')
    app.voice_job_worker.submit(job_id)
    app.voice_job_worker.submit(job_id)  # duplicate, e.g. a retried request
    wait_for_status(app, job_id)
    time.sleep(0.1)  # let a wrongly-duplicated second execution have a chance to run
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# Authentication on polling
# --------------------------------------------------------------------------- #
def test_polling_without_a_key_is_rejected(jobs_client):
    client, app = jobs_client
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None,
                                   language='eng', response_text='hi')
    response = client.get(f'/v1/voice/jobs/{job_id}')
    assert response.status_code == 401


def test_polling_with_the_wrong_key_is_rejected(jobs_client):
    client, app = jobs_client
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None,
                                   language='eng', response_text='hi')
    response = client.get(f'/v1/voice/jobs/{job_id}', headers={'x-api-key': 'wrong'})
    assert response.status_code == 401


def test_a_users_job_is_invisible_to_another_authenticated_identity(jobs_client, auth_headers,
                                                                    monkeypatch):
    """The bound identity is demo-user (see conftest); a job created for a
    different user_id must not be readable even with a valid key."""
    client, app = jobs_client
    job_id = app.voice_jobs.create(user_id='someone-else', session_id=None,
                                   language='eng', response_text='not yours')
    response = client.get(f'/v1/voice/jobs/{job_id}', headers=auth_headers)
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Language preservation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('language', ['eng', 'hin', 'asm'])
def test_job_preserves_the_requested_language_end_to_end(jobs_client, auth_headers, language):
    client, app = jobs_client
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None,
                                   language=language, response_text='some reply text')
    app.voice_job_worker.submit(job_id)
    wait_for_status(app, job_id)
    response = client.get(f'/v1/voice/jobs/{job_id}', headers=auth_headers)
    assert response.status_code == 200
    assert response.json()['language'] == language
