# Testing and the deterministic test mode

**Audience:** anyone running this repository's test suite, and the Backend
team wiring up CI against VoiceBot's contract without provider credentials.

## What actually exists

There are two genuine, config-selectable deterministic providers, plus one
test-side stubbing convention for the third stage. This section describes
what is actually in the repository, not an aspiration.

| Stage | Deterministic mode | How it's selected |
|---|---|---|
| LLM | `MockLLMProvider` (`smriti_voice/llm/mock.py`) | `SMRITI_LLM_PROVIDER=mock`, or built directly in a test via `use_mock_llm(app, ...)` (`tests/integration/test_conversation_scenarios.py`) |
| TTS | `MockTTSProvider` (`smriti_voice/tts/mock.py`) | `SMRITI_TTS_PROVIDER=mock` |
| ASR | No dedicated mock provider class | Tests stub the method directly: `monkeypatch.setattr(app.asr, 'transcribe', ...)` or `app.asr.transcribe = <stub>`. There is no `SMRITI_ASR_PROVIDER=mock` — do not assume one exists. |

### `MockLLMProvider`

Never selected automatically in production — only when
`SMRITI_LLM_PROVIDER=mock` is set explicitly, or a test constructs one
directly. It follows a scripted policy, not a real model:

- `reply='...'` (the default mode): always returns the same fixed text,
  for any number of calls.
- `script=[...]`: a queue of canned `LLMResponse` objects, consumed one per
  call (`tool_call_response(...)` and `tool_then_answer(...)` in
  `test_conversation_scenarios.py` build these).
- `handler=callable`: a Python function computing a response from the
  actual messages/tools for a turn, for tests that need conditional logic
  a fixed script can't express.

Every call is recorded on `provider.calls` (a plain list), so a test can
assert exactly how many times the model was invoked and with what tools/
messages — this is how tests prove, for example, that `welcome()` never
calls the model at all, or that a retried idempotent request only ran the
turn once.

### `MockTTSProvider`

Selected via `SMRITI_TTS_PROVIDER=mock`. Returns a short, valid silent WAV
immediately, with no network call — used throughout the voice-job test
suite so job lifecycle (`queued → processing → completed`), cancellation
races, and expiry can be tested deterministically and fast, without a real
TTS credential or a multi-second real synthesis call.

## How to run the deterministic suite

```bash
python -m pytest -q
```

No environment variables need to be set for this to work: the test
fixtures (`tests/conftest.py`) build an `Application` against an
in-memory SQLite database and construct mock LLM/TTS providers directly
where a test needs one — real provider credentials are never required to
run the suite, and a machine with none configured still gets the full
588+ tests passing.

To run only a subset while iterating:

```bash
python -m pytest -q tests/integration/test_action_state_machine.py
python -m pytest -q tests/integration/test_concurrent_sessions.py -v
```

## What deterministic mode proves

- The conversation state machine (proposal → confirmation → execution →
  result) behaves correctly for every action type, including edge cases
  (expired confirmation, unknown tool, sensitive tool, duplicate
  confirmation) that would be slow or flaky to provoke from a real model.
- The HTTP contract (request/response shapes, status codes, error
  bodies, idempotency, cross-patient isolation, session ownership,
  concurrency safety) is exactly what `INTEGRATION_CONTRACT.md` claims,
  independent of any cloud provider's availability or latency.
- Voice-job lifecycle mechanics (queueing, cancellation races, deadlines,
  restart recovery, expiry) are correct under controlled, repeatable
  timing.

## What deterministic mode does NOT prove

- That a real language model actually resists a prompt-injection attempt
  it hasn't been scripted to resist. The deterministic gates (safety
  screen, tool authorization, confirmation) are what actually prevent a
  manipulated model response from taking effect — `MockLLMProvider` proves
  those gates hold even in a scripted "worst case" (see
  `tests/integration/test_memory_prompt_injection.py`), but it cannot
  prove a real model wouldn't be tricked into trying in the first place.
- That real ASR/TTS providers are reachable, correctly configured, or
  producing intelligible audio for a given language. `MockTTSProvider`
  returns a fixed, silent, valid WAV — it says nothing about whether the
  real Sarvam/Indic Parler-TTS voice actually sounds right, or whether the
  configured model name is still valid on the provider's side (see
  `STAGING_READINESS.md` for the real incident where a deprecated model
  name passed every mocked test while failing on every real call).
- Native-speaker language quality. See `LANGUAGE_SUPPORT.md` — every
  language in this repository is explicitly marked unvalidated unless a
  measured record exists; the deterministic suite cannot change that.
- Real network latency, timeout behavior under load, or genuine
  multi-process concurrency (the mocked suite runs single-process,
  in-memory).

**Mock-provider tests are not a substitute for real staging/provider
validation.** Treat a fully green `pytest -q` run as proof the contract
and state machine are implemented correctly, not as proof the service
works end to end against real providers — that requires the real-HTTP,
real-credential validation described in `STAGING_READINESS.md`.

## Using this in Backend's own CI

A Backend CI pipeline that wants to exercise VoiceBot's contract without
holding a real Sarvam/Groq/OpenAI credential can:

1. Run this repository's own suite as-is (`python -m pytest -q`) to
   confirm the version of VoiceBot it's about to integrate against
   actually passes its own contract tests.
2. Set `SMRITI_LLM_PROVIDER=mock` and `SMRITI_TTS_PROVIDER=mock` when
   running a real HTTP instance of VoiceBot (`uvicorn`) for its own
   integration tests against real endpoints, so conversation/voice calls
   succeed deterministically without needing real provider credentials in
   CI. ASR still needs either a real credential or a locally-provisioned
   offline model in this mode — there is no ASR bypass equivalent to the
   LLM/TTS mocks.
3. Never enable this mode in a staging or production acceptance run and
   call it validated — label it clearly (e.g. in CI job names/logs) as
   "contract smoke test, mock providers," distinct from a real-provider
   acceptance run.
