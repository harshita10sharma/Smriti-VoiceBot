"""Generate a VoiceBot API key.

This is the credential an authorized backend sends as the ``x-api-key`` header
on every request (see ``smriti_voice.api.dependencies.require_api_key``). It
is unrelated to ``HF_TOKEN``, which the server uses to authenticate to the
gated Indic Parler-TTS model and is never exposed through the API.

    python -m smriti_voice.api.generate_key

Prints one key to stdout. Set it as ``SMRITI_API_KEY`` on the server
(environment variable, Render dashboard secret, etc.) — never commit it.
"""
from __future__ import annotations

import secrets


def generate_key() -> str:
    """A URL-safe, cryptographically secure key with 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def main() -> int:
    print(generate_key())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
