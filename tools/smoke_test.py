"""End-to-end smoke test.

Exercises the stack against whatever is actually configured and prints a table
saying, for each check, whether it was REAL (a live provider call) or LOCAL (no
network involved).  It never fabricates a provider result: if a credential is
missing or the network is unreachable, the row says so.

    python tools/smoke_test.py
    python tools/smoke_test.py --user-id demo-user --language eng
"""
from __future__ import annotations

import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', default='smoke-user')
    parser.add_argument('--language', default='eng')
    parser.add_argument('--seed', action='store_true', default=True)
    args = parser.parse_args()

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

    # 5. TTS — only if a provider covers the language.
    if app.languages.can_speak(args.language) and app.config.providers.sarvam_key:
        spoken = app.tts.synthesize('Hello, how are you today?', args.language)
        status = 'PASS' if spoken.available else 'FAIL'
        failures += 0 if spoken.available else 1
        row(f'tts: {args.language}', status, 'REAL',
            f'{spoken.provider} {spoken.size_bytes}B {spoken.latency_ms}ms'
            if spoken.available else str(spoken.unavailable_reason))
    else:
        reason = ('no configured provider speaks this language'
                  if not app.languages.can_speak(args.language) else 'no SARVAM_API_KEY')
        row(f'tts: {args.language}', 'SKIP', 'N/A', reason)

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
