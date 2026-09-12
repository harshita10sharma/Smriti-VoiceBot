"""Per-stage latency metadata (asr_latency_ms/llm_latency_ms/tool_latency_ms/
total_latency_ms on VoiceResponse.metadata) is already exposed by the
existing pipeline -- this pins that it stays populated and sane, and is the
permanent (deterministic, mock-based) regression counterpart to a one-time
real-provider latency measurement recorded in ARCHITECTURE.md/HANDOFF.md,
which used a live Groq call and found the synchronous ASR+conversation
path (785ms-8955ms across 3 runs, ASR stubbed since no ASR provider is
reachable in this environment) does not approach the ~100s gateway timeout
that is the documented reason TTS alone is asynchronous. That real
measurement is not reproduced here as a test, since pinning it would
either require a live credential in CI or would not be testing anything
real once mocked -- this file instead pins the metadata contract so a
regression in whether latency is measured/reported at all would be caught.
"""
from __future__ import annotations

from test_conversation_scenarios import use_mock_llm


def test_conversation_response_reports_llm_latency(app):
    provider = use_mock_llm(app, reply='Hello there.')
    reply = app.conversation.handle(user_id='demo-user', message='hello', language='eng')
    assert reply.metadata.llm_provider == 'mock'
    assert reply.metadata.total_latency_ms >= 0
    assert reply.metadata.llm_latency_ms >= 0


def test_voice_response_reports_per_stage_latency(app):
    import io
    import wave

    from smriti_voice.pipeline import VoicePipeline
    from smriti_voice.schemas import ASRResult

    use_mock_llm(app, reply='Hello there.')
    app.asr.transcribe = lambda *a, **k: ASRResult(
        transcript='hello', language='eng', provider='stub', latency_ms=42)

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\x00\x00' * 1600)
    path_bytes = buf.getvalue()

    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        f.write(path_bytes)
        wav_path = Path(f.name)
    try:
        result = VoicePipeline(app).process(wav_path, user_id='demo-user', language='eng',
                                            speak=False)
    finally:
        wav_path.unlink(missing_ok=True)

    assert result.metadata.asr_latency_ms == 42
    assert result.metadata.llm_provider == 'mock'
    assert result.metadata.total_latency_ms >= result.metadata.asr_latency_ms
