"""Regression coverage for a real defect: OpenAIASR's ISO-639-1 language
hint table mapped 'mni' (Meiteilon/Manipuri) to 'mn', which is the code for
Mongolian -- an unrelated language. Meiteilon has no ISO-639-1 two-letter
code, so per the surrounding code's own documented intent ("for low-resource/
custom NER codes, omit it and let the multilingual model detect"), it must
never be given a two-letter hint at all, let alone the wrong one.

This is currently reachable only when a deployment falls back to OpenAI for
ASR (SMRITI_ASR_PROVIDER=openai, or Sarvam unconfigured while OpenAI is) --
the live pilot uses Sarvam, which has a correct dedicated 'mni-IN' code and
is preferred first (see asr/router.py::online_provider). Fixed regardless,
since the defect is real and would silently degrade Meiteilon transcription
quality the moment OpenAI is used as the ASR provider for it.
"""
from __future__ import annotations

from smriti_voice.asr.providers import OpenAIASR


class _FakeResponse:
    status_code = 200

    def json(self):
        return {'text': 'transcribed text'}


def test_meiteilon_is_never_hinted_as_mongolian(monkeypatch):
    captured = {}

    def _fake_post(url, *, headers, files, data, timeout):
        captured['data'] = data
        return _FakeResponse()

    monkeypatch.setattr('httpx.post', _fake_post)

    provider = OpenAIASR(key='fake-key')
    import io
    from pathlib import Path
    from unittest.mock import mock_open, patch

    with patch.object(Path, 'open', mock_open(read_data=b'RIFF....WAVEfmt ')):
        provider.transcribe(Path('fake.wav'), 'mni')

    assert captured['data'].get('language') != 'mn'
    assert 'language' not in captured['data']


def test_a_language_with_a_genuine_iso_639_1_code_still_gets_hinted(monkeypatch):
    """The fix must not regress the working case: eng really is 'en'."""
    captured = {}

    def _fake_post(url, *, headers, files, data, timeout):
        captured['data'] = data
        return _FakeResponse()

    monkeypatch.setattr('httpx.post', _fake_post)

    provider = OpenAIASR(key='fake-key')
    from pathlib import Path
    from unittest.mock import mock_open, patch

    with patch.object(Path, 'open', mock_open(read_data=b'RIFF....WAVEfmt ')):
        provider.transcribe(Path('fake.wav'), 'eng')

    assert captured['data'].get('language') == 'en'
