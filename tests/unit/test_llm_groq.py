from __future__ import annotations

from types import SimpleNamespace

import pytest

from smriti_voice.config import ProviderConfig
from smriti_voice.exceptions import LLMError
from smriti_voice.llm.base import Message, ToolSpec
from smriti_voice.llm.groq import GroqLLMProvider
from smriti_voice.llm.router import LLMRouter


class FakeCompletions:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error
        self.calls = []

    def create(self, **body):
        self.calls.append(body)
        if self.error:
            raise self.error
        return SimpleNamespace(model_dump=lambda: self.payload)


class FakeGroq:
    completions = None
    init_kwargs = None

    def __init__(self, **kwargs):
        type(self).init_kwargs = kwargs
        self.chat = SimpleNamespace(completions=type(self).completions)


def test_groq_request_and_response(monkeypatch):
    completions = FakeCompletions({
        'choices': [{
            'message': {'content': 'hello', 'tool_calls': [{
                'id': 'call-1',
                'function': {'name': 'get_current_time', 'arguments': '{}'},
            }]},
            'finish_reason': 'tool_calls',
        }],
    })
    FakeGroq.completions = completions
    monkeypatch.setattr('smriti_voice.llm.groq.Groq', FakeGroq)

    provider = GroqLLMProvider('configured', timeout=7.0, max_retries=4, backoff=0.1)
    response = provider.generate(
        [Message('user', 'What time is it?')],
        system='Answer briefly.',
        tools=[ToolSpec('get_current_time', 'Read the current time.', {'type': 'object'})],
        temperature=0.4,
        max_tokens=80,
    )

    assert FakeGroq.init_kwargs == {'api_key': 'configured', 'timeout': 7.0, 'max_retries': 4}
    body = completions.calls[0]
    assert body['model'] == 'qwen/qwen3.8-27b'
    assert body['messages'][0] == {'role': 'system', 'content': 'Answer briefly.'}
    assert body['tools'][0]['function']['name'] == 'get_current_time'
    assert body['temperature'] == 0.4
    assert body['max_tokens'] == 80
    assert response.provider == 'groq'
    assert response.model == 'qwen/qwen3.8-27b'
    assert response.text == 'hello'
    assert response.tool_calls[0].name == 'get_current_time'


def test_groq_errors_do_not_expose_sdk_message(monkeypatch):
    completions = FakeCompletions(error=RuntimeError('secret-key-or-response-body'))
    FakeGroq.completions = completions
    monkeypatch.setattr('smriti_voice.llm.groq.Groq', FakeGroq)

    provider = GroqLLMProvider('configured')
    with pytest.raises(LLMError) as raised:
        provider.generate([Message('user', 'hello')])

    assert 'secret-key-or-response-body' not in str(raised.value)
    assert 'RuntimeError' in str(raised.value)


def test_groq_configuration_and_explicit_router_selection(monkeypatch):
    monkeypatch.setenv('SMRITI_LLM_PROVIDER', 'groq')
    monkeypatch.setenv('GROQ_API_KEY', 'configured')
    monkeypatch.delenv('GROQ_MODEL', raising=False)
    providers = ProviderConfig.from_env()

    assert providers.llm_provider == 'groq'
    assert providers.llm_model_groq == 'qwen/qwen3.8-27b'
    assert providers.configured_providers()['groq'] is True

    config = SimpleNamespace(providers=providers, offline_forced=False)
    router = LLMRouter(config)
    assert router.candidates(needs_tools=False) == ['groq']
    assert router.candidates(needs_tools=True) == ['groq']
