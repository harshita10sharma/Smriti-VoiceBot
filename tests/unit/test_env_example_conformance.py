"""Every environment variable config.py actually reads must be documented
in .env.example -- this is the exact gap a prior integration batch
introduced (new vars added to config.py without updating .env.example) and
this test exists so it can't recur silently.

Two config fields are a deliberate, documented exception: they are loaded
but never actually consumed by any provider-selection code (see
SECURITY.md's Known limitations) -- documenting them in .env.example would
be misleading, not helpful, so they are explicitly excluded here rather
than silently failing this check.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

KNOWN_DEAD_CONFIG = {
    'SMRITI_LOCAL_LLM_PATH',   # ProviderConfig.llm_model_local: never read elsewhere
    'SMRITI_WEATHER_PROVIDER',  # AppConfig.weather_provider: never read elsewhere
}


def _env_vars_read_by_config() -> set[str]:
    source = (ROOT / 'smriti_voice' / 'config.py').read_text(encoding='utf-8')
    return set(re.findall(r"_env_(?:str|int|float|bool)\('([A-Z_0-9]+)'", source))


def _env_vars_documented_in(filename: str) -> set[str]:
    text = (ROOT / filename).read_text(encoding='utf-8')
    return set(re.findall(r'^#?\s*([A-Z][A-Z_0-9]+)=', text, flags=re.MULTILINE))


def test_every_consumed_config_env_var_is_documented_in_env_example():
    consumed = _env_vars_read_by_config() - KNOWN_DEAD_CONFIG
    documented = _env_vars_documented_in('.env.example')
    missing = consumed - documented
    assert not missing, f'.env.example is missing: {sorted(missing)}'


def test_env_staging_example_documents_the_same_variables_as_env_example():
    staging_path = ROOT / '.env.staging.example'
    if not staging_path.exists():
        import pytest
        pytest.skip('.env.staging.example not yet created')
    base = _env_vars_documented_in('.env.example')
    staging = _env_vars_documented_in('.env.staging.example')
    # Staging must cover every variable the base example documents (it may
    # legitimately add extra staging-only guidance, but must not silently
    # drop something an operator would need).
    missing_from_staging = base - staging
    assert not missing_from_staging, (
        f'.env.staging.example is missing variables present in .env.example: '
        f'{sorted(missing_from_staging)}')
