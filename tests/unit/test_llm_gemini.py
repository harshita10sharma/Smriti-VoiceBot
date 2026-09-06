"""Regression tests for two Gemini-specific tool-calling incompatibilities.

1. Gemini's function-declaration Schema rejects `additionalProperties` (HTTP
   400 "Unknown name ... Cannot find field"), which the shared
   Tool.json_schema() always includes.
2. gemini-3.x requires a `thoughtSignature` to be replayed verbatim on a
   function-call part for the *next* turn, or the second round of a
   tool-calling conversation is rejected with "Function call is missing a
   thought_signature in functionCall parts".

Both silently took Gemini out of the LLM router's candidate chain — the
first on any tool-enabled turn, the second only once a tool result needed to
be fed back for a follow-up answer — and the router's own exception handling
made this look like "no LLM provider available" rather than surfacing either
error.
"""
from __future__ import annotations

from smriti_voice.llm.base import Message
from smriti_voice.llm.gemini import _gemini_schema, _to_contents


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


def test_replays_thought_signature_on_the_function_call_part():
    message = Message('assistant', '', tool_calls=[
        {'name': 'get_family_member', 'arguments': {'relation': 'daughter'},
         'call_id': None, 'thought_signature': 'opaque-signature-value'},
    ])
    contents = _to_contents([message])
    part = contents[0]['parts'][0]
    assert part['functionCall']['name'] == 'get_family_member'
    assert part['thoughtSignature'] == 'opaque-signature-value'


def test_omits_thought_signature_when_absent():
    """OpenAI/Sarvam-originated tool_calls dicts never carry this key."""
    message = Message('assistant', '', tool_calls=[
        {'name': 'get_family_member', 'arguments': {}, 'call_id': None},
    ])
    part = _to_contents([message])[0]['parts'][0]
    assert 'thoughtSignature' not in part
