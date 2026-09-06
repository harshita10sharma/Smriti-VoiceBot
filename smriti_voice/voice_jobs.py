"""Async TTS job queue for the voice endpoint.

Indic Parler-TTS can take well over a minute on CPU, which exceeds most
HTTP proxy timeouts (Cloudflare Tunnel's default is ~100s). The voice
endpoint therefore returns immediately with a job id, and a single
background worker thread performs the actual synthesis.

A single worker thread — not a thread pool — is deliberate: the shared
Indic Parler model instance is not safe to call concurrently from multiple
threads, and this process must never hold more than one model instance
(see ARCHITECTURE notes on memory). Every job is synthesised one at a time,
in submission order.

Job state lives in the database, not an in-memory dict, so a status poll
always reflects reality even across a process restart (a job left
'processing' by a crashed process will simply never complete — a real
`failed`/stuck state we don't silently paper over; only the process that
actually enqueued the job runs it, since the worker is in-process).
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .database.connection import Database
from .logging import get_logger

if TYPE_CHECKING:
    from .app import Application

log = get_logger('voice_jobs')

QUEUED = 'queued'
PROCESSING = 'processing'
COMPLETED = 'completed'
FAILED = 'failed'


@dataclass
class VoiceJob:
    job_id: str
    user_id: str
    session_id: str | None
    status: str
    language: str
    response_text: str
    audio_id: str | None
    tts_provider: str | None
    error_code: str | None
    created_at: str
    updated_at: str


class VoiceJobRepository:
    """Persisted job state. The schema is applied by MemoryRepository's own
    migration step; this class only reads/writes rows in the shared database."""

    def __init__(self, database: Database) -> None:
        self.db = database

    def create(self, *, user_id: str, session_id: str | None, language: str,
               response_text: str) -> str:
        job_id = uuid.uuid4().hex
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO voice_jobs (job_id, user_id, session_id, status, language,
                                           response_text)
                   VALUES (?,?,?,?,?,?)""",
                (job_id, user_id, session_id, QUEUED, language, response_text))
        return job_id

    def get(self, job_id: str) -> VoiceJob | None:
        with self.db.connect() as connection:
            row = connection.execute(
                'SELECT * FROM voice_jobs WHERE job_id = ?', (job_id,)).fetchone()
        return VoiceJob(**{key: row[key] for key in row.keys()}) if row else None

    def get_by_audio_id(self, audio_id: str) -> VoiceJob | None:
        """Every audio_id in the system is produced by exactly one code path
        (VoiceJobWorker._process_one, via TTSRouter.synthesize), so this is
        how /v1/audio/{id} establishes who actually owns a given file."""
        with self.db.connect() as connection:
            row = connection.execute(
                'SELECT * FROM voice_jobs WHERE audio_id = ?', (audio_id,)).fetchone()
        return VoiceJob(**{key: row[key] for key in row.keys()}) if row else None

    def mark_processing(self, job_id: str) -> bool:
        """Returns False if the job was not in `queued` state — the caller
        must treat that as "someone already handled this" and not proceed,
        which is what prevents a job from being synthesised twice."""
        with self.db.connect() as connection:
            cursor = connection.execute(
                """UPDATE voice_jobs SET status = ?, updated_at = datetime('now')
                   WHERE job_id = ? AND status = ?""",
                (PROCESSING, job_id, QUEUED))
            return cursor.rowcount > 0

    def mark_completed(self, job_id: str, *, audio_id: str, tts_provider: str | None) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """UPDATE voice_jobs SET status = ?, audio_id = ?, tts_provider = ?,
                                         updated_at = datetime('now')
                   WHERE job_id = ?""",
                (COMPLETED, audio_id, tts_provider, job_id))

    def mark_failed(self, job_id: str, *, error_code: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """UPDATE voice_jobs SET status = ?, error_code = ?, updated_at = datetime('now')
                   WHERE job_id = ?""",
                (FAILED, error_code, job_id))


class VoiceJobWorker:
    """One background thread, one job at a time, forever.

    Holds a live reference to the Application rather than a frozen TTS
    router, so swapping `application.tts` (as some tests do) is honoured on
    the next job — there is exactly one Application per process anyway.
    """

    def __init__(self, application: 'Application', jobs: VoiceJobRepository) -> None:
        self.app = application
        self.jobs = jobs
        self._queue: 'queue.Queue[str]' = queue.Queue()
        self._thread = threading.Thread(target=self._run, name='voice-tts-worker', daemon=True)
        self._thread.start()

    def submit(self, job_id: str) -> None:
        self._queue.put(job_id)

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._process_one(job_id)
            except Exception as exc:  # the worker thread must never die
                log.error('voice_job_worker_crashed',
                          fields={'job_id': job_id, 'error': type(exc).__name__})
                try:
                    self.jobs.mark_failed(job_id, error_code='TTS_WORKER_ERROR')
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    def _process_one(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        if not self.jobs.mark_processing(job_id):
            # Not in `queued` state any more: already picked up (or already
            # finished). Never synthesise the same job twice.
            return

        started = time.perf_counter()
        try:
            result = self.app.tts.synthesize(job.response_text, job.language,
                                             request_id=job_id)
        except Exception as exc:
            log.warning('voice_job_synthesis_raised',
                        fields={'job_id': job_id, 'error': type(exc).__name__})
            self.jobs.mark_failed(job_id, error_code='TTS_UNAVAILABLE')
            return

        if result.available and result.audio_id:
            self.jobs.mark_completed(job_id, audio_id=result.audio_id,
                                     tts_provider=result.provider)
        else:
            self.jobs.mark_failed(job_id, error_code=result.unavailable_reason or 'TTS_UNAVAILABLE')
        log.info('voice_job_finished',
                fields={'job_id': job_id, 'latency_ms': int((time.perf_counter() - started) * 1000)})
