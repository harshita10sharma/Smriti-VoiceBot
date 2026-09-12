"""integration/fixtures/*.json must always validate against the real
Pydantic response models -- this is what keeps the published examples from
silently drifting out of sync with the actual API as the schemas evolve.
"""
from __future__ import annotations

import json
from pathlib import Path

from smriti_voice.schemas import (
    ConversationResponse,
    MemorySyncResponse,
    VoiceJobStatusResponse,
    VoiceResponse,
    WelcomeResponse,
)

FIXTURES = Path(__file__).resolve().parents[2] / 'integration' / 'fixtures'


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def test_welcome_response_fixture_matches_the_real_schema():
    WelcomeResponse.model_validate(_load('welcome_response.json'))


def test_conversation_response_fixture_matches_the_real_schema():
    ConversationResponse.model_validate(_load('conversation_response.json'))


def test_voice_response_fixture_matches_the_real_schema():
    VoiceResponse.model_validate(_load('voice_response.json'))


def test_memory_sync_response_fixture_matches_the_real_schema():
    MemorySyncResponse.model_validate(_load('memory_sync_response.json'))


def test_job_state_fixtures_all_match_the_real_schema():
    for name in ('job_queued.json', 'job_processing.json', 'job_completed.json',
                'job_failed.json', 'job_cancelled.json'):
        VoiceJobStatusResponse.model_validate(_load(name))


def test_job_state_fixtures_cover_the_exhaustive_status_set():
    statuses = {_load(name)['status'] for name in
               ('job_queued.json', 'job_processing.json', 'job_completed.json',
                'job_failed.json', 'job_cancelled.json')}
    assert statuses == {'queued', 'processing', 'completed', 'failed', 'cancelled'}


def test_memory_sync_request_fixture_matches_the_real_schema():
    from smriti_voice.schemas import MemorySyncRequest
    MemorySyncRequest.model_validate(_load('memory_sync_request.json'))


def test_conversation_request_fixture_matches_the_real_schema():
    from smriti_voice.schemas import ConversationRequest
    ConversationRequest.model_validate(_load('conversation_request.json'))


def test_welcome_request_fixture_matches_the_real_schema():
    from smriti_voice.schemas import WelcomeRequest
    WelcomeRequest.model_validate(_load('welcome_request.json'))
