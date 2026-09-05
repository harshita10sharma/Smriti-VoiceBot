"""Indic Parler-TTS text-to-speech provider.

Uses the ai4bharat/indic-parler-tts model from HuggingFace.
The model generates WAV audio at 24kHz.

Configuration via environment variables:
- SMRITI_INDIC_PARLER_ENABLED: set to 'true' to enable this provider
- SMRITI_INDIC_PARLER_MODEL: model name or path (default: ai4bharat/indic-parler-tts)
- SMRITI_INDIC_PARLER_DEVICE: device to use ('auto', 'cpu', 'cuda', etc.)
- SMRITI_INDIC_PARLER_LANGUAGES: comma-separated list of language codes it supports
- SMRITI_INDIC_PARLER_DESCRIPTION: speaker description (default: a calm, neutral voice)
"""

from __future__ import annotations

import os
import time
from typing import Any

import numpy as np

from ..exceptions import ProviderNotConfigured, ProviderTimeout, TTSError
from ..logging import get_logger, redact
from ..schemas import TTSResult

log = get_logger('tts.indic_parler')

# Supported languages by Indic Parler-TTS model (internal codes)
SUPPORTED_LANGUAGES = {
    'asm': 'Assamese',
    'ben': 'Bengali',
    'brx': 'Bodo',
    'eng': 'English',
    'guj': 'Gujarati',
    'hin': 'Hindi',
    'kan': 'Kannada',
    'kas': 'Kashmiri',
    'mai': 'Maithili',
    'mal': 'Malayalam',
    'mni': 'Manipuri',
    'mar': 'Marathi',
    'npi': 'Nepali',
    'ori': 'Odia',
    'pan': 'Punjabi',
    'san': 'Sanskrit',
    'sat': 'Santali',
    'snd': 'Sindhi',
    'tam': 'Tamil',
    'tel': 'Telugu',
    'urd': 'Urdu',
}

# Internal Smriti codes remain stable at the provider boundary. Indic Parler
# detects the spoken language from the transcript, so this mapping documents
# the supported model languages without rewriting the user's text.
MODEL_LANGUAGE_NAMES = {
    'asm': 'Assamese',
    'ben': 'Bengali',
    'brx': 'Bodo',
    'eng': 'English',
    'guj': 'Gujarati',
    'hin': 'Hindi',
    'kan': 'Kannada',
    'kas': 'Kashmiri',
    'mai': 'Maithili',
    'mal': 'Malayalam',
    'mni': 'Manipuri',
    'mar': 'Marathi',
    'npi': 'Nepali',
    'ori': 'Odia',
    'pan': 'Punjabi',
    'san': 'Sanskrit',
    'sat': 'Santali',
    'snd': 'Sindhi',
    'tam': 'Tamil',
    'tel': 'Telugu',
    'urd': 'Urdu',
}

# Default speaker description - can be overridden via config
DEFAULT_DESCRIPTION = "a calm, neutral Indian voice"


class IndicParlerTTSProvider:
    name = 'indic_parler'
    online = False  # Runs locally, but may download model on first use

    def __init__(
        self,
        *,
        model_name: str | None = None,
        device: str | None = None,
        languages: str | None = None,
        description: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        # Configuration from parameters or environment
        self.model_name = (model_name or os.getenv('SMRITI_INDIC_PARLER_MODEL', 'ai4bharat/indic-parler-tts')).strip()
        self.device_str = (device or os.getenv('SMRITI_INDIC_PARLER_DEVICE', 'auto')).strip()
        declared_langs = (languages or os.getenv('SMRITI_INDIC_PARLER_LANGUAGES', ''))
        self.language_codes = frozenset(
            code.strip() for code in declared_langs.split(',') if code.strip() in SUPPORTED_LANGUAGES
        )
        self.description = (description or os.getenv('SMRITI_INDIC_PARLER_DESCRIPTION', DEFAULT_DESCRIPTION)).strip()
        self.timeout = timeout

        # Lazy initialization
        self._model = None
        self._tokenizer = None
        self._description_tokenizer = None
        self._device = None

        # Validate that at least one language is configured
        if not self.language_codes:
            log.warning(
                'indic_parler_no_languages_configured',
                fields={'languages': declared_langs or '(none)'}
            )

    @property
    def device(self):
        """Resolve the torch device based on configuration and availability."""
        if self._device is None:
            # Import torch here to avoid hard dependency if not used
            import torch
            if self.device_str == 'auto':
                self._device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            else:
                self._device = torch.device(self.device_str)
        return self._device

    def _load_model(self) -> None:
        """Lazy load the model and tokenizer."""
        if self._model is not None:
            return

        try:
            from parler_tts import ParlerTTSForConditionalGeneration
            from transformers import AutoTokenizer
            import torch

            log.info(
                'indic_parler_loading_model',
                fields={'model': self.model_name, 'device': self.device}
            )
            start = time.perf_counter()

            self._model = ParlerTTSForConditionalGeneration.from_pretrained(self.model_name)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            description_tokenizer_name = self._model.config.text_encoder._name_or_path
            self._description_tokenizer = AutoTokenizer.from_pretrained(description_tokenizer_name)
            self._model.to(self.device)

            load_time = time.perf_counter() - start
            log.info(
                'indic_parler_model_loaded',
                fields={'load_time_seconds': round(load_time, 2)}
            )
        except Exception as exc:
            log.error(
                'indic_parler_model_load_failed',
                fields={'error': type(exc).__name__, 'model': self.model_name}
            )
            raise ProviderNotConfigured(
                f'Failed to load Indic Parler-TTS model {self.model_name!r}: {exc}'
            ) from exc

    def supports(self, language: str) -> bool:
        """Check if the given language is supported by this provider."""
        return language in self.language_codes

    def synthesize(self, text: str, language: str, *, voice: str | None = None) -> TTSResult:
        """Synthesize speech using Indic Parler-TTS.

        Args:
            text: The text to synthesize
            language: Language code (must be in supported languages)
            voice: Ignored (speaker is controlled by description)

        Returns:
            TTSResult with audio as WAV bytes
        """
        if not self.supports(language):
            raise TTSError(f'Indic Parler-TTS does not support language {language!r}')

        if not (text or '').strip():
            raise TTSError('Nothing to speak')

        # Ensure model is loaded
        try:
            self._load_model()
        except Exception as exc:
            # Re-raise as TTSError for consistency with router expectations
            raise TTSError(f'Indic Parler-TTS not available: {exc}') from exc

        start = time.perf_counter()
        try:
            # Import torch for torch.no_grad() context
            import torch

            # Indic Parler uses the prompt tokenizer for the transcript and a
            # separate text-encoder tokenizer for the speaker description.
            prompt_inputs = self._tokenizer(text, return_tensors='pt').to(self.device)
            description_inputs = self._description_tokenizer(
                self.description, return_tensors='pt'
            ).to(self.device)

            # Generate audio
            with torch.no_grad():
                generation = self._model.generate(
                    input_ids=description_inputs.input_ids,
                    attention_mask=description_inputs.attention_mask,
                    prompt_input_ids=prompt_inputs.input_ids,
                    prompt_attention_mask=prompt_inputs.attention_mask,
                )

            # Extract audio array (shape: [1, samples])
            audio_array = generation.cpu().numpy().squeeze()
            sample_rate = int(self._model.config.sampling_rate)

            # Convert to 16-bit PCM WAV
            audio_bytes = self._float_to_wav(audio_array, sample_rate=sample_rate)

        except Exception as exc:
            log.warning(
                'indic_parler_synthesis_failed',
                fields={
                    'language': language,
                    'error': type(exc).__name__,
                    'text_preview': redact(text[:50]) if text else ''
                }
            )
            raise TTSError(f'Indic Parler-TTS synthesis failed: {exc}') from exc

        latency_ms = int((time.perf_counter() - start) * 1000)

        return TTSResult(
            audio=audio_bytes,
            language=language,
            voice=self.description,
            provider=self.name,
            model=self.model_name,
            offline=True,
            mime_type='audio/wav',
            sample_rate=sample_rate,
            latency_ms=latency_ms
        )

    def _float_to_wav(self, float_audio: np.ndarray, sample_rate: int) -> bytes:
        """Convert float32 audio array to 16-bit PCM WAV bytes.

        Args:
            float_audio: Audio samples as float32 in range [-1, 1]
            sample_rate: Sampling rate in Hz

        Returns:
            WAV audio as bytes
        """
        import io
        import wave

        # Ensure audio is in valid range
        float_audio = np.clip(float_audio, -1.0, 1.0)

        # Convert to 16-bit PCM
        audio_int16 = (float_audio * 32767).astype(np.int16)

        # Write WAV to bytes buffer
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)          # Mono
            wav_file.setsampwidth(2)          # 16-bit = 2 bytes
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_int16.tobytes())

        return buffer.getvalue()