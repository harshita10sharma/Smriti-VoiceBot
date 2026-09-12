"""SMRITI_ALLOW_UNAUTHENTICATED is a real fail-open path (every protected
route served as 'anonymous', no patient binding at all -- see
api/dependencies.py:require_api_key). It exists for local development only.

This guard makes that a config-load-time hard failure, not a silent
foot-gun, unless the operator has also explicitly declared SMRITI_ENV=
development -- two separate, deliberate opt-ins required, not one flag.
"""
from __future__ import annotations

import pytest

from smriti_voice.config import AppConfig
from smriti_voice.exceptions import ConfigurationError


def test_allow_unauthenticated_without_dev_env_fails_closed(monkeypatch):
    monkeypatch.setenv('SMRITI_ALLOW_UNAUTHENTICATED', '1')
    monkeypatch.delenv('SMRITI_ENV', raising=False)
    with pytest.raises(ConfigurationError) as excinfo:
        AppConfig.load()
    assert excinfo.value.code == 'UNSAFE_AUTH_CONFIGURATION'


def test_allow_unauthenticated_with_production_env_fails_closed(monkeypatch):
    monkeypatch.setenv('SMRITI_ALLOW_UNAUTHENTICATED', '1')
    monkeypatch.setenv('SMRITI_ENV', 'production')
    with pytest.raises(ConfigurationError):
        AppConfig.load()


def test_allow_unauthenticated_with_explicit_development_env_is_permitted(monkeypatch):
    monkeypatch.setenv('SMRITI_ALLOW_UNAUTHENTICATED', '1')
    monkeypatch.setenv('SMRITI_ENV', 'development')
    config = AppConfig.load()
    assert config.allow_unauthenticated is True
    assert config.environment == 'development'


def test_normal_deployment_is_unaffected(monkeypatch):
    """The existing single-user elder-1 deployment never sets
    SMRITI_ALLOW_UNAUTHENTICATED, so this guard must never fire for it."""
    monkeypatch.delenv('SMRITI_ALLOW_UNAUTHENTICATED', raising=False)
    monkeypatch.delenv('SMRITI_ENV', raising=False)
    config = AppConfig.load()
    assert config.allow_unauthenticated is False
    assert config.environment == 'production'


def test_environment_defaults_to_production_when_unset(monkeypatch):
    monkeypatch.delenv('SMRITI_ENV', raising=False)
    assert AppConfig.load().environment == 'production'


def test_environment_value_is_case_insensitive(monkeypatch):
    monkeypatch.setenv('SMRITI_ALLOW_UNAUTHENTICATED', '1')
    monkeypatch.setenv('SMRITI_ENV', 'Development')
    config = AppConfig.load()
    assert config.environment == 'development'
    assert config.allow_unauthenticated is True
