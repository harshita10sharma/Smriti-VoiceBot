"""Multi-worker safety cannot be enforced by reliable in-process detection
(sibling uvicorn worker processes share no memory and signal nothing to
each other), so this is a best-effort, clearly-labeled guard: a loud
startup log line when a common multi-process env var indicates more than
one worker, plus the equivalent check in tools/validate_config.py. Neither
claims to catch every way of misconfiguring the deployment -- see
SECURITY.md.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def test_no_warning_when_no_worker_env_var_is_set(app, monkeypatch, caplog):
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('WEB_CONCURRENCY', raising=False)
    monkeypatch.delenv('UVICORN_WORKERS', raising=False)
    with caplog.at_level(logging.ERROR, logger='smriti.api'):
        with TestClient(create_app(app)):
            pass
    assert 'unsafe_multi_worker_configuration_detected' not in caplog.text


def test_warns_when_web_concurrency_indicates_multiple_workers(app, monkeypatch, caplog):
    from smriti_voice.api.app import create_app
    monkeypatch.setenv('WEB_CONCURRENCY', '4')
    with caplog.at_level(logging.ERROR, logger='smriti.api'):
        with TestClient(create_app(app)):
            pass
    assert 'unsafe_multi_worker_configuration_detected' in caplog.text


def test_single_worker_value_does_not_warn(app, monkeypatch, caplog):
    from smriti_voice.api.app import create_app
    monkeypatch.setenv('WEB_CONCURRENCY', '1')
    with caplog.at_level(logging.ERROR, logger='smriti.api'):
        with TestClient(create_app(app)):
            pass
    assert 'unsafe_multi_worker_configuration_detected' not in caplog.text


def test_non_numeric_worker_env_var_is_ignored_not_crashed(app, monkeypatch, caplog):
    from smriti_voice.api.app import create_app
    monkeypatch.setenv('WEB_CONCURRENCY', 'auto')
    with caplog.at_level(logging.ERROR, logger='smriti.api'):
        with TestClient(create_app(app)):
            pass  # must not raise
    assert 'unsafe_multi_worker_configuration_detected' not in caplog.text


# --------------------------------------------------------------------------- #
# tools/validate_config.py's equivalent, pre-deploy check
# --------------------------------------------------------------------------- #
def _subprocess_env(overrides: dict) -> dict:
    """validate_config.py imports the full Application, including
    numpy/torch-backed providers. Spawned while the parent pytest process
    already holds its own BLAS thread pools (as happens when the full suite
    runs), an unbounded child thread pool can hit a real
    'OpenBLAS ... Memory allocation still failed' error that has nothing to
    do with the behaviour under test. Capping the child's thread pools is
    the standard fix for that nested-process contention, not a workaround
    for a defect in the code being tested."""
    import os
    env = {**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
          'MKL_NUM_THREADS': '1'}
    env.update(overrides)
    return env


def test_validate_config_flags_multi_worker_env_var():
    env = _subprocess_env({'WEB_CONCURRENCY': '4'})
    result = subprocess.run([sys.executable, str(ROOT / 'tools' / 'validate_config.py')],
                            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
    assert 'WEB_CONCURRENCY=4 is safe for this deployment' in result.stdout
    assert result.returncode == 1  # a hard error in the pre-deploy check


def test_validate_config_passes_with_no_worker_env_var_set():
    env = _subprocess_env({})
    env.pop('WEB_CONCURRENCY', None)
    env.pop('UVICORN_WORKERS', None)
    result = subprocess.run([sys.executable, str(ROOT / 'tools' / 'validate_config.py')],
                            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
    assert 'no multi-worker env var detected' in result.stdout
