"""Structured JSON logging with secret redaction.

Every log line is one JSON object.  Values that look like credentials are
replaced before the record is emitted, so an accidental ``logger.info(config)``
cannot leak a key into a log file or a container's stdout.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any

_SECRET_KEY_HINTS = ('key', 'token', 'secret', 'password', 'authorization', 'api_subscription')
_SECRET_VALUE_PATTERNS = [
    re.compile(r'\bsk[-_][A-Za-z0-9_\-]{8,}\b'),
    re.compile(r'\bBearer\s+[A-Za-z0-9._\-]{8,}\b', re.IGNORECASE),
    re.compile(r'\bAIza[0-9A-Za-z_\-]{20,}\b'),
]
# Never let these reach a log record even if a caller passes them explicitly.
_FORBIDDEN_FIELDS = {'audio', 'raw_audio', 'audio_bytes', 'audio_base64', 'wav', 'pcm'}

REDACTED = '[REDACTED]'


def redact(value: Any, key: str | None = None) -> Any:
    """Recursively redact secrets.  Used by the logger and by ``/v1/health``."""
    if key and any(hint in key.lower() for hint in _SECRET_KEY_HINTS):
        return REDACTED if value else None
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items() if k not in _FORBIDDEN_FIELDS}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        out = value
        for pattern in _SECRET_VALUE_PATTERNS:
            out = pattern.sub(REDACTED, out)
        return out
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            'ts': self.formatTime(record, '%Y-%m-%dT%H:%M:%S'),
            'level': record.levelname,
            'logger': record.name,
            'message': redact(record.getMessage()),
        }
        for key, value in getattr(record, 'extra_fields', {}).items():
            if key not in _FORBIDDEN_FIELDS:
                payload[key] = redact(value, key)
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _ContextLogger(logging.LoggerAdapter):
    """Attaches ``request_id``/``session_id`` style fields to every record."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        fields = dict(self.extra or {})
        fields.update(kwargs.pop('fields', {}) or {})
        kwargs.setdefault('extra', {})['extra_fields'] = fields
        return msg, kwargs


_configured = False


def configure(level: str | None = None) -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger('smriti')
    root.handlers[:] = [handler]
    root.setLevel((level or os.getenv('SMRITI_LOG_LEVEL', 'INFO')).upper())
    root.propagate = False
    _configured = True


def get_logger(name: str, **context: Any) -> _ContextLogger:
    configure()
    return _ContextLogger(logging.getLogger(f'smriti.{name}'), context)
