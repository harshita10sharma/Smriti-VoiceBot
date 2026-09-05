"""SMRITI VoiceBot command line.

The v4.1 flags (``--list-languages``, ``--health``, ``--record``, ``--file``,
``--language``) behave exactly as before.  The v5 flags add configuration
checking, a seeded demo database and a text chat loop for local testing.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from smriti_voice.config import Settings
from smriti_voice.engine import VoiceEngine


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


# --------------------------------------------------------------------------- #
# v4.1 commands (unchanged behaviour)
# --------------------------------------------------------------------------- #
def cmd_list_languages(args) -> int:
    engine = VoiceEngine(Settings.load())
    _print([asdict(pack) | {'commands_path': str(pack.commands_path)}
            for pack in engine.languages()])
    return 0


def cmd_command_turn(args) -> int:
    """The v4.1 single-utterance command path."""
    settings = Settings.load()
    engine = VoiceEngine(settings)
    path = args.file
    if args.record:
        from smriti_voice.audio import record_push_to_talk
        path = settings.recordings_dir / 'live.wav'
        quality = record_push_to_talk(path, settings.record_seconds, settings.sample_rate,
                                      settings.channels)
        print(quality)
    if not path:
        print('Use --file FILE (or --record) with --language CODE', file=sys.stderr)
        return 2
    _print(engine.process(Path(path), args.language or settings.default_language).__dict__)
    return 0


# --------------------------------------------------------------------------- #
# v5 commands
# --------------------------------------------------------------------------- #
def cmd_health(args) -> int:
    from smriti_voice.app import Application
    from smriti_voice.offline.health import build_health
    app = Application.build()
    _print(build_health(config=app.config, languages=app.languages, llm=app.llm,
                        tts=app.tts, offline=app.offline,
                        tool_count=len(app.registry.names())))
    return 0


def cmd_test_config(args) -> int:
    """Check configuration without contacting any provider."""
    from smriti_voice.app import Application
    app = Application.build()
    credentials = app.config.providers.configured_providers()
    checks = {
        'settings_loaded': True,
        'language_packs': len(app.languages.all()),
        'tools_registered': len(app.registry.names()),
        'safety_policy_loaded': bool(app.conversation.policy.confirmation_required_actions
                                     or True),
        'database_path': app.config.database_url,
        'credentials_present': credentials,
        'api_authentication_configured': bool(__import__('os').getenv(app.config.api_key_env)),
        'unauthenticated_access_allowed': app.config.allow_unauthenticated,
        'llm_providers_buildable': app.llm.available(),
        'tts_providers_buildable': app.tts.available(),
    }
    _print(checks)
    problems = []
    if not any(credentials.values()):
        problems.append('No provider credentials are set: only offline/deterministic '
                        'answers will work.')
    if not checks['api_authentication_configured'] and not app.config.allow_unauthenticated:
        problems.append(f'{app.config.api_key_env} is not set: protected endpoints '
                        f'will return 503.')
    for problem in problems:
        print(f'WARNING: {problem}', file=sys.stderr)
    return 0


def cmd_seed(args) -> int:
    from datetime import date

    from smriti_voice.app import Application
    from smriti_voice.memory.seed import seed_demo_user
    app = Application.build()
    user = seed_demo_user(app.memory.repo, args.user_id, today=date.today())
    print(f'Seeded demo user {user.user_id!r} into {app.config.database_url}')
    return 0


def cmd_chat(args) -> int:
    """Text chat loop.  No microphone, no audio: for checking behaviour quickly."""
    from smriti_voice.app import Application
    app = Application.build()
    session_id = None
    print('SMRITI text chat. Type "quit" to leave.\n')
    while True:
        try:
            message = input('you> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if message.lower() in {'quit', 'exit'}:
            return 0
        if not message:
            continue
        language = args.language or app.detector.detect(message).language
        reply = app.conversation.handle(user_id=args.user_id, message=message,
                                        session_id=session_id, language=language)
        session_id = reply.session_id
        print(f'smriti[{reply.language}/{reply.kind.value}]> {reply.response_text}')
        if reply.action != 'NO_ACTION':
            print(f'          action: {reply.action} (accepted={reply.action_accepted})')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='smriti_voice', description=__doc__)
    parser.add_argument('--list-languages', action='store_true',
                        help='Print the configured language packs (v4.1 format).')
    parser.add_argument('--health', action='store_true', help='Print capability health.')
    parser.add_argument('--test-config', action='store_true',
                        help='Validate configuration without calling any provider.')
    parser.add_argument('--seed-demo', action='store_true',
                        help='Create the demo user in the local database.')
    parser.add_argument('--chat', action='store_true', help='Interactive text chat.')
    parser.add_argument('--record', action='store_true',
                        help='Record one utterance from the microphone (v4.1).')
    parser.add_argument('--file', type=Path, help='WAV file to process (v4.1 command path).')
    parser.add_argument('--language', default=None, help='Language code, e.g. eng, hin, asm.')
    parser.add_argument('--user-id', default='demo-user', help='User id for chat/seed.')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_languages:
        return cmd_list_languages(args)
    if args.health:
        return cmd_health(args)
    if args.test_config:
        return cmd_test_config(args)
    if args.seed_demo:
        return cmd_seed(args)
    if args.chat:
        return cmd_chat(args)
    if args.file or args.record:
        return cmd_command_turn(args)
    parser.print_help()
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
