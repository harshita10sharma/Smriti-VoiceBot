"""The v4.1 test suite, preserved verbatim except for the repository-root path
and the API-key fixture (the endpoint now fails closed, so the key must be set).

These nine tests passed on commit 4f6c0c1 before the migration and must keep
passing after it.
"""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from smriti_voice.config import Settings,LanguageRegistry
from smriti_voice.intents import IntentRouter
from smriti_voice.actions import SafeActionGate,Action
from smriti_voice.api import app
from smriti_voice.engine import VoiceEngine
from smriti_voice.asr import SarvamASR
ROOT=Path(__file__).resolve().parents[2]

def test_registry(): assert len(LanguageRegistry(ROOT/'language_packs').all())>=10

def test_safe_commands_are_recognized_locally():
    r=IntentRouter(ROOT/'language_packs/commands_eng.json')
    assert r.classify('medicine').accepted and r.classify('medicine').intent == 'OPEN_MEDICINE'
    assert r.classify('open medicine').accepted and r.classify('open medicine').intent == 'OPEN_MEDICINE'
    assert r.classify('show medicine').accepted and r.classify('show medicine').intent == 'OPEN_MEDICINE'
    fam=r.classify('show my family')
    assert fam.accepted and fam.intent == 'OPEN_MY_PEOPLE'

def test_unsafe_commands_fail_closed():
    r=IntentRouter(ROOT/'language_packs/commands_eng.json')
    unsafe_phrases=[
        'delete my medicine','change my medicine','change my dosage','delete medicine','remove my medicine',
        'cancel my medicine','stop my medicine','transfer money','call 9876543210','call +91 98765 43210',
        'remove medicine','change my family','delete family','cancel family','stop my today','delete my people',
        'change my prescription','remove my prescription','cancel my routine','transfer account money'
    ]
    for phrase in unsafe_phrases:
        result=r.classify(phrase)
        assert not result.accepted, f'{phrase!r} unexpectedly accepted as {result.action}'
        assert result.action == 'NO_ACTION', f'{phrase!r} should resolve to NO_ACTION'

def test_api_rejects_empty_or_malformed_wav(monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEY','test-key')
    from smriti_voice.api.app import create_app
    client=TestClient(create_app())
    response=client.post('/v1/command', data={'language':'eng'}, files={'audio_wav':('bad.wav', b'not-a-wav', 'audio/wav')}, headers={'x-api-key':'test-key'})
    assert response.status_code == 415

    response=client.post('/v1/command', data={'language':'eng'}, files={'audio_wav':('empty.wav', b'', 'audio/wav')}, headers={'x-api-key':'test-key'})
    assert response.status_code == 400
def test_empty_phrases_fail_closed():
 r=IntentRouter(ROOT/'language_packs/commands_mni.json')
 m=r.classify('anything')
 assert not m.accepted and m.action=='NO_ACTION'
def test_action_gate():
 g=SafeActionGate(); assert g.authorize('DROP_DATABASE','x').action==Action.NO_ACTION

def test_engine_uses_requested_language_for_sarvam_fallback(monkeypatch,tmp_path):
    class StubSarvamASR(SarvamASR):
        def transcribe(self,wav,language=None):
            return 'open medicine', language, 1.0

    monkeypatch.setenv('SARVAM_API_KEY','configured-for-test')
    engine=VoiceEngine(Settings.load())
    engine._online_provider=lambda language: StubSarvamASR('configured-for-test')

    result=engine.process(tmp_path/'input.wav','eng')

    assert result.language == 'eng'
    assert result.provider == 'sarvam'
    assert result.accepted
    assert result.action == 'OPEN_MEDICINE'


def test_language_listing_serializable():
    import json
    from dataclasses import asdict
    from smriti_voice.config import Settings
    from smriti_voice.engine import VoiceEngine
    e=VoiceEngine(Settings.load())
    payload=[asdict(x) | {"commands_path": str(x.commands_path)} for x in e.languages()]
    json.dumps(payload, ensure_ascii=False)


def test_api_import_and_health():
    from smriti_voice.api import app
    assert app.title == "Smriti Voice API"
