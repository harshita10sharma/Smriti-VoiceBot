"""Unit tests for the ASR provider factory and the local IndicConformer path.

Regression coverage for a real bug found during manual provisioning: warmup()
used to call ``local_dir.mkdir(parents=True, exist_ok=True)`` before invoking
``onnx_asr.load_model``. The installed onnx-asr release treats an *existing*
local directory as a complete offline cache and never downloads into it, so a
freshly created (empty) directory silently disabled the Hugging Face download
and every local ASR provisioning attempt failed with ``config.json not
found``. warmup() must never create that directory itself.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from smriti_voice.asr.providers import ASRError, IndicConformerASR, ProviderFactory


def test_warmup_does_not_pre_create_the_local_directory(tmp_path, monkeypatch):
    monkeypatch.setenv('SMRITI_PROVISIONING', '1')
    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    target = tmp_path / 'indicconformer-brx'
    assert not target.exists()

    mock_onnx_asr = Mock()
    mock_onnx_asr.load_model = Mock(side_effect=lambda *a, **k: (
        # Assert the directory still does not exist at call time: nothing in
        # warmup() must have created it ahead of the (mocked) download.
        (_ for _ in ()).throw(AssertionError('local_dir must not pre-exist'))
        if target.exists() else Mock()
    ))

    provider = IndicConformerASR('some/model-id', target, 'brx')
    with patch.dict(sys.modules, {'onnx_asr': mock_onnx_asr}):
        provider.warmup()

    mock_onnx_asr.load_model.assert_called_once()
    assert not target.exists(), 'warmup() must leave directory creation to onnx_asr'


def test_warmup_pops_offline_env_when_provisioning(tmp_path, monkeypatch):
    monkeypatch.setenv('SMRITI_PROVISIONING', '1')
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    mock_onnx_asr = Mock()
    mock_onnx_asr.load_model = Mock(return_value=Mock())
    provider = IndicConformerASR('some/model-id', tmp_path / 'm', 'brx')
    with patch.dict(sys.modules, {'onnx_asr': mock_onnx_asr}):
        provider.warmup()
    assert 'HF_HUB_OFFLINE' not in __import__('os').environ


def test_warmup_forces_offline_when_not_provisioning(tmp_path, monkeypatch):
    monkeypatch.delenv('SMRITI_PROVISIONING', raising=False)
    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    mock_onnx_asr = Mock()
    mock_onnx_asr.load_model = Mock(return_value=Mock())
    provider = IndicConformerASR('some/model-id', tmp_path / 'm', 'brx')
    with patch.dict(sys.modules, {'onnx_asr': mock_onnx_asr}):
        provider.warmup()
    import os
    assert os.environ.get('HF_HUB_OFFLINE') == '1'


def test_warmup_raises_asr_error_when_onnx_asr_not_installed(tmp_path):
    provider = IndicConformerASR('some/model-id', tmp_path / 'm', 'brx')
    with patch.dict(sys.modules, {'onnx_asr': None}):
        with pytest.raises(ASRError, match='onnx-asr is not installed'):
            provider.warmup()


def test_provider_factory_requires_model_id_for_local_providers(tmp_path):
    factory = ProviderFactory(tmp_path)
    with pytest.raises(ASRError, match='No model_id configured'):
        factory.create('indicconformer', 'brx', None)


def test_provider_factory_rejects_unknown_provider(tmp_path):
    factory = ProviderFactory(tmp_path)
    with pytest.raises(ASRError, match='Unknown ASR provider'):
        factory.create('not_a_real_provider', 'eng', None)
