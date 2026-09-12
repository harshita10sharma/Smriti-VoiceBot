"""Voice job hardening added on top of the existing async TTS pipeline:

- a bounded worker queue that fails a job cleanly (QUEUE_OVERLOADED) instead
  of accepting submissions forever, and
- startup recovery of jobs orphaned by a previous process crash/restart
  (INTERRUPTED_BY_RESTART), so a client is never left polling 'processing'
  forever for a job nothing will ever finish.

Both are additive: neither changes the behaviour already covered by
tests/integration/test_voice_jobs.py for the normal, non-overloaded,
non-crashed case.
"""
from __future__ import annotations

from smriti_voice.voice_jobs import FAILED, PROCESSING, QUEUED, VoiceJobRepository, VoiceJobWorker
from smriti_voice.tts.router import AudioStore


def test_submit_beyond_queue_capacity_fails_the_job_cleanly(app):
    # Build a second worker with capacity 1 sharing the same job repository,
    # so we can fill its queue deterministically without racing the real
    # background worker thread that Application.build() already started.
    worker = VoiceJobWorker.__new__(VoiceJobWorker)
    worker.app = app
    worker.jobs = app.voice_jobs
    import queue as queue_module
    worker._queue = queue_module.Queue(maxsize=1)

    job_a = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                  response_text='first')
    job_b = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                  response_text='second')

    assert worker.submit(job_a) is True   # fills the capacity-1 queue
    assert worker.submit(job_b) is False  # queue is full -> rejected, not blocked

    failed = app.voice_jobs.get(job_b)
    assert failed.status == FAILED
    assert failed.error_code == 'QUEUE_OVERLOADED'

    # The first job's row is untouched by the second submission's failure.
    still_queued = app.voice_jobs.get(job_a)
    assert still_queued.status == QUEUED


def test_recover_stale_jobs_marks_orphaned_queued_and_processing_jobs_failed(app):
    repo: VoiceJobRepository = app.voice_jobs
    queued_job = repo.create(user_id='demo-user', session_id=None, language='eng',
                             response_text='orphaned while queued')
    processing_job = repo.create(user_id='demo-user', session_id=None, language='eng',
                                 response_text='orphaned mid-synthesis')
    assert repo.mark_processing(processing_job) is True  # simulate a crash mid-flight

    recovered = repo.recover_stale_jobs()
    assert recovered == 2

    for job_id in (queued_job, processing_job):
        job = repo.get(job_id)
        assert job.status == FAILED
        assert job.error_code == 'INTERRUPTED_BY_RESTART'


def test_recover_stale_jobs_never_touches_terminal_jobs(app):
    repo: VoiceJobRepository = app.voice_jobs
    done = repo.create(user_id='demo-user', session_id=None, language='eng', response_text='ok')
    repo.mark_processing(done)  # real usage always transitions through 'processing' first
    repo.mark_completed(done, audio_id='abc123', tts_provider='mock')

    recovered = repo.recover_stale_jobs()
    assert recovered == 0

    job = repo.get(done)
    assert job.status == 'completed'
    assert job.audio_id == 'abc123'  # untouched


# --------------------------------------------------------------------------- #
# Independent re-audit (integration-hardening phase 6): expired audio must
# not be retrievable even if prune() -- which only runs opportunistically on
# the next write -- has not run since the file aged out.
# --------------------------------------------------------------------------- #
def test_expired_audio_is_not_retrievable_even_without_a_new_write(tmp_path):
    import os
    import time

    store = AudioStore(tmp_path, retention_minutes=1)  # clamped to 60s minimum internally
    audio_id = store.put(b'RIFF....WAVEfmt ' + b'\x00' * 20)
    path = store.directory / f'{audio_id}.wav'
    assert path.is_file()

    # Age the file past the retention window without triggering prune() via
    # another write -- exactly the scenario prune()'s opportunistic design
    # does not cover on its own.
    old = time.time() - store.retention_s - 5
    os.utime(path, (old, old))

    assert store.path_for(audio_id) is None
    assert not path.exists()  # also cleaned up, not just refused


def test_fresh_audio_within_retention_is_still_retrievable(tmp_path):
    store = AudioStore(tmp_path, retention_minutes=15)
    audio_id = store.put(b'RIFF....WAVEfmt ' + b'\x00' * 20)
    assert store.path_for(audio_id) is not None


def test_path_for_rejects_non_hex_audio_id_without_touching_disk(tmp_path):
    store = AudioStore(tmp_path, retention_minutes=15)
    assert store.path_for('../../etc/passwd') is None
    assert store.path_for('') is None
