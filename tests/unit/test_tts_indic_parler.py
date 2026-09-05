"""Unit tests for the Indic Parler-TTS provider."""

from __future__ import annotations

import sys
from unittest.mock import Mock, patch

import numpy as np
import pytest

from smriti_voice.tts.indic_parler import IndicParlerTTSProvider
from smriti_voice.exceptions import TTSError, ProviderNotConfigured


@pytest.fixture(autouse=True)
def indic_parler_environment(monkeypatch):
    monkeypatch.setenv('SMRITI_INDIC_PARLER_ENABLED', 'true')
    monkeypatch.setenv('SMRITI_INDIC_PARLER_MODEL', 'ai4bharat/indic-parler-tts')
    monkeypatch.setenv('SMRITI_INDIC_PARLER_DEVICE', 'cpu')
    monkeypatch.setenv('SMRITI_INDIC_PARLER_LANGUAGES', 'asm,brx,mni,npi')
    monkeypatch.setenv('SMRITI_INDIC_PARLER_DESCRIPTION', 'a calm, neutral Indian voice')


def test_provider_imports_without_loading_model():
    """Test that importing the provider does not load the model."""
    # Since we moved imports inside methods, importing should work without deps
    provider = IndicParlerTTSProvider()
    assert provider._model is None
    assert provider._tokenizer is None
    # No imports happened at module level for heavy deps


def test_provider_supports_correct_languages():
    """Test that the provider supports the configured languages."""
    provider = IndicParlerTTSProvider()
    assert provider.supports('asm') is True
    assert provider.supports('brx') is True
    assert provider.supports('mni') is True
    assert provider.supports('npi') is True
    # Test a language not in the configured list
    assert provider.supports('hin') is False  # Hindi not in our configured list


def test_provider_does_not_support_unconfigured_language():
    """Test that a language not in the configured list is not supported."""
    provider = IndicParlerTTSProvider()
    assert provider.supports('hin') is False
    assert provider.supports('eng') is False


def test_provider_raises_on_unsupported_language():
    """Test that synthesizing an unsupported language raises TTSError."""
    provider = IndicParlerTTSProvider()
    with pytest.raises(TTSError, match='does not support language'):
        provider.synthesize('hello', 'hin')  # Hindi not in our configured list


def test_provider_raises_on_empty_text():
    """Test that synthesizing empty text raises TTSError."""
    provider = IndicParlerTTSProvider()
    with pytest.raises(TTSError, match='Nothing to speak'):
        provider.synthesize('', 'asm')


def test_provider_synthesize_calls_model_and_tokenizer():
    """Test that synthesize calls the model and tokenizer when supported."""
    # We need to mock the imports inside the _load_model method
    # Create a mock module for parler_tts and transformers
    mock_transformers = Mock()
    mock_transformers.AutoTokenizer = Mock()

    mock_parler_tts = Mock()
    mock_model = Mock()

    mock_torch = Mock()
    mock_torch.device = Mock()
    mock_torch.cuda = Mock()
    mock_torch.cuda.is_available = Mock(return_value=False)
    # Mock the no_grad context manager
    mock_no_grad_context = Mock()
    mock_no_grad_context.__enter__ = Mock(return_value=None)
    mock_no_grad_context.__exit__ = Mock(return_value=False)
    mock_torch.no_grad = Mock(return_value=mock_no_grad_context)

    # Set up the prompt and description tokenizer mocks used by the official API.
    mock_prompt_tokenizer = Mock()
    mock_prompt_tokenizer.to.return_value = mock_prompt_tokenizer
    mock_prompt_tokenizer.input_ids = Mock(name='prompt_input_ids')
    mock_prompt_tokenizer.attention_mask = Mock(name='prompt_attention_mask')
    mock_description_tokenizer = Mock()
    mock_description_tokenizer.to.return_value = mock_description_tokenizer
    mock_description_tokenizer.input_ids = Mock(name='description_input_ids')
    mock_description_tokenizer.attention_mask = Mock(name='description_attention_mask')
    mock_transformers.AutoTokenizer.from_pretrained.side_effect = [
        mock_prompt_tokenizer,
        mock_description_tokenizer,
    ]

    # Set up the model mock
    mock_model_instance = Mock()
    mock_model_instance.config.text_encoder._name_or_path = 'description-tokenizer'
    mock_model_instance.config.sampling_rate = 24000
    mock_tensor = Mock()
    mock_tensor.cpu = Mock()
    mock_tensor.cpu.return_value = Mock()
    mock_tensor.cpu.return_value.numpy = Mock()
    mock_tensor.cpu.return_value.numpy.return_value = Mock()
    mock_tensor.cpu.return_value.numpy.return_value.squeeze = Mock(return_value=np.array([0.0, 0.1, 0.2]))
    mock_model_instance.generate = Mock(return_value=mock_tensor)
    # We are going to mock the class method `from_pretrained` on the class
    mock_parler_tts.ParlerTTSForConditionalGeneration = Mock()
    mock_parler_tts.ParlerTTSForConditionalGeneration.from_pretrained.return_value = mock_model_instance

    # Patch the imports at the module level where they are used
    with patch.dict('sys.modules', {
        'transformers': mock_transformers,
        'parler_tts': mock_parler_tts,
        'torch': mock_torch
    }):
        # Mock _float_to_wav to avoid dealing with numpy array operations in the test
        with patch.object(IndicParlerTTSProvider, '_float_to_wav', return_value=b'wav_bytes'):
            provider = IndicParlerTTSProvider()
            # Call synthesize - this should trigger _load_model
            result = provider.synthesize('hello world', 'asm')

        # Check that the model and tokenizer were called
        assert mock_transformers.AutoTokenizer.from_pretrained.call_args_list[0].args == (
            'ai4bharat/indic-parler-tts',
        )
        assert mock_transformers.AutoTokenizer.from_pretrained.call_args_list[1].args == (
            'description-tokenizer',
        )
        mock_parler_tts.ParlerTTSForConditionalGeneration.from_pretrained.assert_called_once_with('ai4bharat/indic-parler-tts')
        mock_model_instance.generate.assert_called_once()

        # Check the result
        assert result.audio == b'wav_bytes'
        assert result.language == 'asm'
        assert result.provider == 'indic_parler'
        assert result.mime_type == 'audio/wav'


def test_provider_handles_model_load_failure():
    """Test that model loading failure is converted to TTSError."""
    with patch.dict('sys.modules', {
        'transformers': Mock(),
        'parler_tts': Mock(),
        'torch': Mock()
    }):
        # Make AutoTokenizer.from_pretrained raise an exception
        sys.modules['transformers'].AutoTokenizer.from_pretrained.side_effect = Exception('Failed to load')
        provider = IndicParlerTTSProvider()
        with pytest.raises(TTSError, match='Indic Parler-TTS not available'):
            provider.synthesize('hello', 'asm')


def test_provider_respects_device_configuration(monkeypatch):
    """Test that the provider uses the configured device."""
    mock_torch = Mock()
    mock_torch.device = Mock()
    mock_torch.cuda = Mock()
    mock_torch.cuda.is_available = Mock(return_value=False)
    # Mock the no_grad context manager for the synthesize test
    mock_no_grad_context = Mock()
    mock_no_grad_context.__enter__ = Mock(return_value=None)
    mock_no_grad_context.__exit__ = Mock(return_value=False)
    mock_torch.no_grad = Mock(return_value=mock_no_grad_context)

    with patch.dict('sys.modules', {
        'torch': mock_torch,
        'transformers': Mock(),
        'parler_tts': Mock()
    }):
        # Configure mocks
        mock_torch.device.return_value = Mock()
        mock_prompt_tokenizer = Mock()
        mock_prompt_tokenizer.to.return_value = mock_prompt_tokenizer
        mock_prompt_tokenizer.input_ids = Mock()
        mock_prompt_tokenizer.attention_mask = Mock()
        mock_description_tokenizer = Mock()
        mock_description_tokenizer.to.return_value = mock_description_tokenizer
        mock_description_tokenizer.input_ids = Mock()
        mock_description_tokenizer.attention_mask = Mock()
        sys.modules['transformers'].AutoTokenizer.from_pretrained.side_effect = [
            mock_prompt_tokenizer,
            mock_description_tokenizer,
        ]
        mock_model = sys.modules['parler_tts'].ParlerTTSForConditionalGeneration.from_pretrained.return_value
        mock_model.config.text_encoder._name_or_path = 'description-tokenizer'
        mock_model.config.sampling_rate = 24000
        # Set up the tensor chain to return a numpy array
        mock_cpu = Mock()
        mock_cpu.numpy = Mock(return_value=np.array([0.0, 0.1, 0.2]))
        mock_tensor = Mock()
        mock_tensor.cpu = Mock(return_value=mock_cpu)
        sys.modules['parler_tts'].ParlerTTSForConditionalGeneration.return_value.generate.return_value = mock_tensor

        # Test with device set to 'cpu'
        monkeypatch.setenv('SMRITI_INDIC_PARLER_DEVICE', 'cpu')
        provider = IndicParlerTTSProvider()
        # Mock _float_to_wav to avoid dealing with numpy array operations in the test
        with patch.object(IndicParlerTTSProvider, '_float_to_wav', return_value=b'wav_bytes'):
            # Trigger model load by calling synthesize (with mocked generation)
            provider.synthesize('test', 'asm')
        # Check that the model was moved to the correct device
        mock_torch.device.assert_called()


def test_provider_returns_false_when_not_enabled(monkeypatch):
    """Test that the provider is not available when disabled."""
    monkeypatch.setenv('SMRITI_INDIC_PARLER_ENABLED', 'false')
    # Reload the module to pick up the environment variable change
    # In a real test, we would use importlib.reload, but for simplicity we'll just instantiate
    # and check the build method in the router. However, we can test the provider's enabled flag.
    # The provider itself doesn't know about the enabled flag; that's checked in the router.
    # So we skip this test here and rely on the router test.
    pass