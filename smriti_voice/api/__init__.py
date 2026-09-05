"""HTTP API.

``from smriti_voice.api import app`` is the v4.1 import path and still works.
"""
from .app import app, create_app

__all__ = ['app', 'create_app']
