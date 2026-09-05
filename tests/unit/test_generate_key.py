"""Unit test for the VoiceBot API key generator."""
from __future__ import annotations

from smriti_voice.api.generate_key import generate_key


def test_generate_key_is_long_and_url_safe():
    key = generate_key()
    assert len(key) >= 32
    assert all(ch.isalnum() or ch in '-_' for ch in key)


def test_generate_key_is_random_each_call():
    keys = {generate_key() for _ in range(10)}
    assert len(keys) == 10
