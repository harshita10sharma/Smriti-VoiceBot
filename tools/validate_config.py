"""Validate every configuration file and the runtime wiring, without a network.

Exit code 0 = everything needed to run is present and consistent.
Exit code 1 = at least one hard error. Warnings alone do not fail the run.

    python tools/validate_config.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smriti_voice.app import Application                    # noqa: E402
from smriti_voice.config import LanguageRegistry, Settings  # noqa: E402
from smriti_voice.intents import IntentRouter               # noqa: E402
from smriti_voice.safety.policy import REFUSALS, get_policy  # noqa: E402

errors: list[str] = []
warnings: list[str] = []


def check(label: str, condition: bool, detail: str = '', *, hard: bool = True) -> None:
    if condition:
        print(f'  ok    {label}{" — " + detail if detail else ""}')
        return
    print(f'  FAIL  {label}{" — " + detail if detail else ""}')
    (errors if hard else warnings).append(f'{label}: {detail}')


def main() -> int:
    print('Configuration files')
    settings = Settings.load()
    check('config/settings.json loads', True, f'sample_rate={settings.sample_rate}')
    for name in ('safety.json', 'language_validation.json'):
        path = ROOT / 'config' / name
        try:
            json.loads(path.read_text(encoding='utf-8'))
            check(f'config/{name} is valid JSON', True)
        except Exception as exc:
            check(f'config/{name} is valid JSON', False, str(exc))

    print('\nLanguage packs')
    registry = LanguageRegistry(settings.language_pack_dir)
    packs = registry.all()
    check('ner_languages.json loads', bool(packs), f'{len(packs)} packs')
    for pack in packs:
        exists = pack.commands_path.exists()
        check(f'pack file for {pack.code}', exists, str(pack.commands_path.name))
        if exists:
            try:
                IntentRouter(pack.commands_path)
            except Exception as exc:
                check(f'router builds for {pack.code}', False, str(exc))
        if pack.status == 'validated_local' and not pack.model_id:
            check(f'{pack.code} declares a model id', False, 'validated_local without model_id')

    print('\nSafety policy')
    policy = get_policy()
    check('safety policy loads', bool(policy.confirmation_required_actions))
    for utterance in ('delete my medicine', 'change my dosage', 'transfer money',
                      'call 9876543210', 'delete my family', 'change my game'):
        verdict = policy.screen_utterance(utterance)
        check(f'refuses {utterance!r}', not verdict.allowed, verdict.category or '')
    for utterance in ('open play', 'show my family', "what's today", 'help', 'stop',
                      'medicine', 'call Bina'):
        verdict = policy.screen_utterance(utterance)
        check(f'allows {utterance!r}', verdict.allowed)
    for language in ('eng', 'hin', 'asm', 'ben'):
        missing = [key for key, table in REFUSALS.items() if language not in table]
        check(f'refusal wording for {language}', not missing, ', '.join(missing))

    print('\nRuntime wiring')
    app = Application.build()
    check('application builds', True, f'{len(app.registry.names())} tools registered')
    check('language matrix builds', len(app.languages.all()) == len(packs))
    check('database opens', app.memory.repo is not None, app.config.database_url)

    print('\nCredentials and providers (booleans only)')
    credentials = app.config.providers.configured_providers()
    for name, present in credentials.items():
        check(f'{name} credential present', present, '', hard=False)
    check('any LLM provider buildable',
          any(ready for name, ready in app.llm.available().items() if name != 'mock'),
          'only the mock provider is available', hard=False)
    check('any TTS provider buildable',
          any(app.tts.available().get(name) for name in ('sarvam', 'local', 'indic_parler')),
          'no real TTS provider is available', hard=False)

    print('\nIndic Parler-TTS (asm/brx/mni/npi)')
    if app.config.providers.indic_parler_enabled:
        hf_token_set = bool(os.getenv('HF_TOKEN', '').strip())
        check('HF_TOKEN is set', hf_token_set,
              'ai4bharat/indic-parler-tts is a gated model; without HF_TOKEN the first '
              'download/load will fail with 401')
        check('indic_parler provider builds', app.tts.available().get('indic_parler', False),
              'see server logs for the exact provider error')
    else:
        check('SMRITI_INDIC_PARLER_ENABLED is set', False,
              'asm/brx/mni/npi will have no voice output until this is enabled', hard=False)

    print('\nAPI authentication')
    raw_key_map = os.getenv('SMRITI_API_KEYS', '').strip()
    key_set = bool(os.getenv(app.config.api_key_env))
    if raw_key_map:
        from smriti_voice.api.dependencies import _parse_api_key_map
        try:
            key_map = _parse_api_key_map(raw_key_map)
        except ValueError as exc:
            check('SMRITI_API_KEYS is valid', False, str(exc))
            key_map = {}
        else:
            check('SMRITI_API_KEYS is valid', True,
                  f'{len(key_map)} identity(ies) configured — multi-user mode')
            check('SMRITI_API_KEYS identities are unique',
                  len(set(key_map.values())) == len(key_map),
                  'two keys map to the same user id, which is redundant but not unsafe',
                  hard=False)
        if key_set or os.getenv('SMRITI_AUTH_USER_ID', '').strip():
            warnings.append('SMRITI_API_KEYS is set alongside SMRITI_API_KEY/SMRITI_AUTH_USER_ID '
                             '— multi-user mode takes priority and the single-user variables '
                             'are ignored; unset them to avoid confusion')
    else:
        if key_set:
            check(f'{app.config.api_key_env} is set', True, 'single-user mode')
        else:
            check(f'{app.config.api_key_env} is set', app.config.allow_unauthenticated,
                  'protected endpoints will return 503 until this is set', hard=False)
        identity_set = bool(os.getenv('SMRITI_AUTH_USER_ID', '').strip())
        check('SMRITI_AUTH_USER_ID is set', identity_set,
              'protected personal endpoints will return 503 until this is set', hard=False)
        if key_set and identity_set:
            warnings.append(
                'Running in single-user mode: this deployment serves exactly one elder '
                '(the identity bound to SMRITI_AUTH_USER_ID). To serve multiple elderly '
                'users through one backend, configure SMRITI_API_KEYS instead.')
    if app.config.allow_unauthenticated:
        warnings.append('SMRITI_ALLOW_UNAUTHENTICATED=1 — never do this on a network-facing host')

    print('\n' + '=' * 70)
    print(f'errors: {len(errors)}   warnings: {len(warnings)}')
    for warning in warnings:
        print(f'  WARNING  {warning}')
    for error in errors:
        print(f'  ERROR    {error}')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
