"""End-to-end smoke test.

Two independent modes:

- **Local/in-process (default)**: builds an ``Application`` directly against
  this machine's configuration and exercises the stack in-process. Exercises
  the stack against whatever is actually configured and prints a table
  saying, for each check, whether it was REAL (a live provider call) or
  LOCAL (no network involved). It never fabricates a provider result: if a
  credential is missing or the network is unreachable, the row says so.
  Safe for CI (no real deployment needed, no `--base-url`).

- **Remote/HTTP (``--base-url``)**: targets an already-running deployment
  (staging or the pilot) over real HTTP, using only the safe, non-
  destructive operations documented in INTEGRATION_CONTRACT.md: health,
  languages, an authenticated welcome, an authenticated text turn, and (if
  ``--voice``) a synthetic-audio voice turn through job polling and audio
  retrieval. The API credential is read from ``SMRITI_SMOKE_API_KEY``
  (never from a CLI argument, so it can never appear in shell history) and
  is never printed. Response bodies (which may carry transcripts) are not
  printed unless ``--show-text`` is passed explicitly.

    python tools/smoke_test.py
    python tools/smoke_test.py --user-id demo-user --language eng
    SMRITI_SMOKE_API_KEY=... python tools/smoke_test.py --base-url https://staging.example.com --voice
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from datetime import date

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))

from smriti_voice.app import Application            # noqa: E402
from smriti_voice.memory.seed import seed_demo_user  # noqa: E402
from smriti_voice.schemas import TurnKind            # noqa: E402

SAFE_COMMANDS = ['open play', 'show my family', "what's today", 'help', 'stop', 'open medicine']
UNSAFE = ['delete my medicine', 'change my dosage', 'transfer money', 'call 9876543210',
          'delete my family', 'change my game']


def row(name: str, status: str, kind: str, detail: str = '') -> None:
    print(f'{name:<44} {status:<8} {kind:<7} {detail}')


def _silent_wav(seconds: float = 0.5, sample_rate: int = 16000) -> bytes:
    frames = int(sample_rate * seconds)
    data = b'\x00\x00' * frames
    header = b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE'
    header += b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    header += b'data' + struct.pack('<I', len(data))
    return header + data


def run_remote(base_url: str, *, user_id: str, language: str, timeout: float,
               include_voice: bool, show_text: bool) -> int:
    """Safe, non-destructive checks against a real running deployment.
    Never prints the API key; response text is redacted unless --show-text."""
    import time as time_module

    import httpx

    api_key = os.environ.get('SMRITI_SMOKE_API_KEY', '').strip()
    if not api_key:
        print('SMRITI_SMOKE_API_KEY is not set -- refusing to run remote checks '
              '(the credential must come from the environment only, never a CLI flag).')
        return 1

    headers = {'x-api-key': api_key}
    failures = 0
    print(f'{"CHECK":<44} {"RESULT":<8} {"KIND":<7} DETAIL')
    print('-' * 100)

    def redact(text: str) -> str:
        return text if show_text else f'<{len(text)} chars, use --show-text to display>'

    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        try:
            resp = client.get('/v1/health')
            ok = resp.status_code == 200 and resp.json().get('status') == 'ok'
        except Exception as exc:
            ok, resp = False, None
            row('GET /v1/health', 'FAIL', 'REAL', f'{type(exc).__name__}')
            failures += 1
        else:
            failures += 0 if ok else 1
            row('GET /v1/health', 'PASS' if ok else 'FAIL', 'REAL', f'status={resp.status_code}')
        if not ok:
            print('-' * 100)
            print('Health check failed; aborting remaining remote checks.')
            return 1

        try:
            resp = client.get('/v1/languages')
            ok = resp.status_code == 200 and 'languages' in resp.json()
        except Exception as exc:
            ok = False
            row('GET /v1/languages', 'FAIL', 'REAL', type(exc).__name__)
        else:
            row('GET /v1/languages', 'PASS' if ok else 'FAIL', 'REAL',
                f'{resp.json().get("count", "?")} languages' if ok else f'status={resp.status_code}')
        failures += 0 if ok else 1

        try:
            resp = client.post('/v1/conversation/welcome', headers=headers,
                               json={'user_id': user_id, 'language': language})
            ok = resp.status_code == 200 and resp.json().get('kind') == 'WELCOME'
            session_id = resp.json().get('session_id') if ok else None
        except Exception as exc:
            ok, session_id = False, None
            row('POST /v1/conversation/welcome', 'FAIL', 'REAL', type(exc).__name__)
        else:
            row('POST /v1/conversation/welcome', 'PASS' if ok else 'FAIL', 'REAL',
                f'status={resp.status_code}')
        failures += 0 if ok else 1

        try:
            resp = client.post('/v1/conversation', headers=headers,
                               json={'user_id': user_id, 'session_id': session_id,
                                    'message': "What is my daughter's name?",
                                    'language': language})
            ok = resp.status_code == 200
        except Exception as exc:
            ok = False
            row('POST /v1/conversation', 'FAIL', 'REAL', type(exc).__name__)
        else:
            detail = redact(resp.json().get('response_text', '')) if ok else f'status={resp.status_code}'
            row('POST /v1/conversation', 'PASS' if ok else 'FAIL', 'REAL', detail)
        failures += 0 if ok else 1

        if include_voice:
            try:
                files = {'audio_wav': ('smoke.wav', _silent_wav(), 'audio/wav')}
                data = {'user_id': user_id, 'session_id': session_id or '', 'language': language,
                        'speak': 'true'}
                resp = client.post('/v1/conversation/voice', headers=headers, files=files, data=data)
                ok = resp.status_code == 200
                job_id = resp.json().get('job_id') if ok else None
            except Exception as exc:
                ok, job_id = False, None
                row('POST /v1/conversation/voice', 'FAIL', 'REAL', type(exc).__name__)
            else:
                row('POST /v1/conversation/voice', 'PASS' if ok else 'FAIL', 'REAL',
                    f'job_id={"set" if job_id else "none"}')
            failures += 0 if ok else 1

            if job_id:
                deadline = time_module.monotonic() + 30
                status, audio_id = None, None
                while time_module.monotonic() < deadline:
                    poll = client.get(f'/v1/voice/jobs/{job_id}', headers=headers)
                    body = poll.json() if poll.status_code == 200 else {}
                    status = body.get('status')
                    if status in ('completed', 'failed', 'cancelled'):
                        audio_id = body.get('audio_id')
                        break
                    time_module.sleep(1)
                ok = status in ('completed', 'failed')  # either is a truthful terminal state
                row('GET /v1/voice/jobs/{id} (poll)', 'PASS' if ok else 'FAIL', 'REAL',
                    f'status={status}')
                failures += 0 if ok else 1

                if status == 'completed' and audio_id:
                    audio_resp = client.get(f'/v1/audio/{audio_id}', headers=headers)
                    ok = (audio_resp.status_code == 200
                         and audio_resp.headers.get('content-type') == 'audio/wav')
                    row('GET /v1/audio/{id}', 'PASS' if ok else 'FAIL', 'REAL',
                       f'status={audio_resp.status_code}')
                    failures += 0 if ok else 1

    print('-' * 100)
    print(f'{"FAILURES":<44} {failures}')
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--user-id', default='smoke-user')
    parser.add_argument('--language', default='eng')
    parser.add_argument('--seed', action='store_true', default=True)
    parser.add_argument('--base-url', default=os.environ.get('SMRITI_SMOKE_BASE_URL', ''),
                        help='Target a real deployment over HTTP instead of running in-process. '
                             'API key comes from SMRITI_SMOKE_API_KEY (environment only).')
    parser.add_argument('--voice', action='store_true',
                        help='(--base-url mode only) also exercise the voice turn + job + audio.')
    parser.add_argument('--timeout', type=float, default=30.0)
    parser.add_argument('--show-text', action='store_true',
                        help='Print response_text (may contain personal data). Off by default.')
    args = parser.parse_args()

    if args.base_url:
        return run_remote(args.base_url, user_id=args.user_id, language=args.language,
                          timeout=args.timeout, include_voice=args.voice,
                          show_text=args.show_text)

    app = Application.build()
    if args.seed:
        seed_demo_user(app.memory.repo, args.user_id, today=date.today())

    failures = 0
    print(f'{"CHECK":<44} {"RESULT":<8} {"KIND":<7} DETAIL')
    print('-' * 100)

    # 1. Deterministic command path — never touches a network.
    for utterance in SAFE_COMMANDS:
        reply = app.conversation.handle(user_id=args.user_id, message=utterance,
                                        language=args.language)
        ok = reply.action_accepted or reply.requires_confirmation
        failures += 0 if ok else 1
        row(f'command: {utterance!r}', 'PASS' if ok else 'FAIL', 'LOCAL', reply.action)

    # 2. Safety — must refuse.
    for utterance in UNSAFE:
        reply = app.conversation.handle(user_id=args.user_id, message=utterance,
                                        language=args.language)
        ok = reply.kind is TurnKind.REFUSAL and not reply.action_accepted
        failures += 0 if ok else 1
        row(f'refuses: {utterance!r}', 'PASS' if ok else 'FAIL', 'LOCAL',
            reply.safety.category if reply.safety else '')

    # 3. Personal memory — reads the database, no model needed for the tool itself.
    from smriti_voice.safety.authorization import Principal
    from smriti_voice.schemas import ToolCall
    from smriti_voice.tools.registry import ToolContext
    context = ToolContext(memory=app.memory, weather=app.conversation.weather)
    result = app.registry.execute(ToolCall(name='get_family_member',
                                           arguments={'relation': 'daughter'}),
                                  Principal(args.user_id), context)
    ok = result.ok and result.data.get('count', 0) > 0
    failures += 0 if ok else 1
    row('memory: daughter lookup', 'PASS' if ok else 'FAIL', 'LOCAL',
        result.data['members'][0]['name'] if ok else str(result.error))

    # 4. LLM — only if a provider is genuinely configured.
    available = app.llm.available()
    configured = [name for name, ready in available.items() if ready and name != 'mock']
    if configured:
        from smriti_voice.llm.base import Message
        try:
            response = app.llm.generate([Message('user', 'Say hello in one short sentence.')],
                                        system='Reply in English, one short sentence.')
            row('llm: live generation', 'PASS', 'REAL',
                f'{response.provider} {response.latency_ms}ms')
        except Exception as exc:
            failures += 1
            row('llm: live generation', 'FAIL', 'REAL', f'{type(exc).__name__}: {exc}')
    else:
        row('llm: live generation', 'SKIP', 'N/A', 'no LLM credentials configured')

    # 5. TTS — attempt synthesis whenever any provider is actually configured
    # (Sarvam or Indic Parler); `can_speak()` only reflects Sarvam's language
    # list, so it must not gate this check or Indic Parler-only languages
    # (asm, brx, mni, npi) would never be exercised here.
    tts_ready = any(app.tts.available().get(name) for name in ('sarvam', 'indic_parler', 'local'))
    if tts_ready:
        spoken = app.tts.synthesize('Hello, how are you today?', args.language)
        if spoken.available:
            failures += 0
            row(f'tts: {args.language}', 'PASS', 'REAL',
                f'{spoken.provider} {spoken.size_bytes}B {spoken.latency_ms}ms')
        elif spoken.unavailable_reason == 'NO_TTS_PROVIDER_SUPPORTS_LANGUAGE':
            row(f'tts: {args.language}', 'SKIP', 'N/A',
                'no configured provider speaks this language')
        else:
            failures += 1
            row(f'tts: {args.language}', 'FAIL', 'REAL', str(spoken.unavailable_reason))
    else:
        row(f'tts: {args.language}', 'SKIP', 'N/A', 'no TTS provider configured')

    # 6. Weather — a real network call when reachable.
    weather_result = app.registry.execute(ToolCall(name='get_weather', arguments={}),
                                          Principal(args.user_id), context)
    data = weather_result.data or {}
    if data.get('available'):
        row('weather: live reading', 'PASS', 'REAL',
            f"{data.get('temperature_c')}C {data.get('condition')}")
    else:
        row('weather: live reading', 'SKIP', 'REAL', str(data.get('reason')))

    print('-' * 100)
    print(f'{"FAILURES":<44} {failures}')
    print('\nREAL = a live provider call was made. LOCAL = no network involved. '
          'SKIP = not configured or unreachable.')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
