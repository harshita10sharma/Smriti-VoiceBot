"""Regression test: Gemini's function-declaration Schema rejects
`additionalProperties` (HTTP 400 "Unknown name ... Cannot find field"), which
the shared Tool.json_schema() always includes. This silently took Gemini out
of the LLM router's candidate chain on any tool-enabled conversation turn.
"""
from __future__ import annotations

from smriti_voice.llm.gemini import _gemini_schema


def test_strips_additional_properties_at_top_level():
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    assert 'additionalProperties' not in _gemini_schema(schema)


def test_strips_additional_properties_recursively():
    schema = {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'contact': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {'name': {'type': 'string'}},
            },
        },
    }
    sanitised = _gemini_schema(schema)
    assert 'additionalProperties' not in sanitised
    assert 'additionalProperties' not in sanitised['properties']['contact']


def test_preserves_everything_else():
    schema = {'type': 'object', 'properties': {'name': {'type': 'string', 'maxLength': 80}},
              'required': ['name'], 'description': 'A contact.'}
    assert _gemini_schema(schema) == schema
