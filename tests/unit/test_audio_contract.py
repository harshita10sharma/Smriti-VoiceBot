"""The real, enforced WAV upload contract -- verified against the actual
validator, not documented aspirationally. validate_wav_bytes enforces:
container (RIFF/WAVE header), a minimum byte size, presence of a 'data'
chunk, a maximum byte size, and (new, fail-open) a maximum duration parsed
via the stdlib wave module. It does NOT enforce sample rate, channel
count, or bit depth -- those are left to the ASR provider, and this file
deliberately does not claim otherwise.
"""
from __future__ import annotations

import io
import struct
import wave

import pytest

from smriti_voice.exceptions import AudioError
from smriti_voice.pipeline import validate_wav_bytes

MAX_BYTES = 10 * 1024 * 1024


def _wav(*, seconds: float = 1.0, sample_rate: int = 16000, channels: int = 1,
        sampwidth: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sampwidth)
        handle.setframerate(sample_rate)
        handle.writeframes(b'\x00' * sampwidth * channels * int(sample_rate * seconds))
    return buf.getvalue()


def test_valid_wav_is_accepted():
    validate_wav_bytes(_wav(), max_bytes=MAX_BYTES)  # must not raise


def test_empty_payload_is_rejected():
    with pytest.raises(AudioError, match='empty'):
        validate_wav_bytes(b'', max_bytes=MAX_BYTES)


def test_non_wav_payload_is_rejected():
    with pytest.raises(AudioError, match='valid WAV'):
        validate_wav_bytes(b'not a wav file at all, just plain bytes here', max_bytes=MAX_BYTES)


def test_truncated_header_is_rejected():
    with pytest.raises(AudioError):
        validate_wav_bytes(b'RIFF' + b'\x00' * 4 + b'WAVE', max_bytes=MAX_BYTES)


def test_oversized_payload_is_rejected():
    with pytest.raises(AudioError, match='larger than'):
        validate_wav_bytes(_wav(seconds=0.01), max_bytes=10)


def test_missing_data_chunk_is_rejected():
    # A well-formed, non-truncated RIFF/WAVE/fmt header with no 'data'
    # marker anywhere (padded past MIN_WAV_BYTES so this exercises the
    # 'missing audio data' check specifically, not the truncation check).
    header = b'RIFF' + struct.pack('<I', 36) + b'WAVE' + b'fmt ' + b'\x00' * 32
    with pytest.raises(AudioError, match='missing audio data'):
        validate_wav_bytes(header, max_bytes=MAX_BYTES)


# --------------------------------------------------------------------------- #
# Duration limit -- fail-open, best-effort, only rejects a positively
# confirmed excessive duration
# --------------------------------------------------------------------------- #
def test_duration_within_limit_is_accepted():
    validate_wav_bytes(_wav(seconds=2.0), max_bytes=MAX_BYTES, max_duration_s=5.0)


def test_duration_exceeding_the_limit_is_rejected():
    with pytest.raises(AudioError, match='exceeds the'):
        validate_wav_bytes(_wav(seconds=10.0), max_bytes=MAX_BYTES, max_duration_s=5.0)


def test_no_duration_limit_means_no_duration_check():
    long_wav = _wav(seconds=1000.0)
    validate_wav_bytes(long_wav, max_bytes=len(long_wav) + 1, max_duration_s=None)


def test_duration_check_is_fail_open_for_unparseable_but_otherwise_valid_wav():
    """A file that passes the container/data checks but that Python's
    `wave` module can't parse for duration (e.g. a non-PCM/compressed
    codec inside a valid RIFF/WAVE/data shell) must not be rejected purely
    because duration couldn't be computed -- only a *confirmed* excess
    duration is ever a rejection reason."""
    header = b'RIFF' + struct.pack('<I', 100) + b'WAVE' + b'fmt ' + struct.pack('<I', 16)
    header += struct.pack('<HHIIHH', 999, 1, 16000, 32000, 2, 16)  # bogus/unknown format tag
    header += b'data' + struct.pack('<I', 8) + b'\x00' * 8
    validate_wav_bytes(header, max_bytes=MAX_BYTES, max_duration_s=1.0)  # must not raise


# --------------------------------------------------------------------------- #
# Different channel counts / bit depths are accepted (not enforced, by design)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('channels,sampwidth,sample_rate', [
    (1, 2, 16000), (1, 2, 8000), (1, 2, 44100), (2, 2, 16000), (1, 1, 16000),
])
def test_various_real_world_wav_shapes_are_all_accepted(channels, sampwidth, sample_rate):
    """Sample rate, channel count and bit depth are deliberately NOT
    enforced here -- an ASR provider handles or rejects those. This locks
    in that fact so a future change doesn't silently start rejecting a
    real device's recording format without a deliberate decision to do so."""
    validate_wav_bytes(_wav(channels=channels, sampwidth=sampwidth, sample_rate=sample_rate),
                       max_bytes=MAX_BYTES)


# --------------------------------------------------------------------------- #
# HTTP-layer mapping
# --------------------------------------------------------------------------- #
def test_http_status_mapping_for_upload_errors(client, auth_headers):
    empty = client.post('/v1/conversation/voice',
                        data={'user_id': 'demo-user', 'language': 'eng'},
                        files={'audio_wav': ('a.wav', b'', 'audio/wav')}, headers=auth_headers)
    assert empty.status_code == 400

    bad = client.post('/v1/conversation/voice',
                      data={'user_id': 'demo-user', 'language': 'eng'},
                      files={'audio_wav': ('a.wav', b'not a wav', 'audio/wav')},
                      headers=auth_headers)
    assert bad.status_code == 415

    oversized = client.post('/v1/conversation/voice',
                            data={'user_id': 'demo-user', 'language': 'eng'},
                            files={'audio_wav': ('a.wav', _wav(seconds=1000.0), 'audio/wav')},
                            headers=auth_headers)
    assert oversized.status_code == 413  # exceeds SMRITI_MAX_WAV_DURATION_S (default 60s)
