# Architecture

## The organising principle

> The language model **proposes**. Deterministic code **authorises**.

Nothing the model emits — text, a tool name, an argument — reaches a database write, a
phone dialler or a screen transition without passing a rule written in Python and covered
by a test. The prompt asks the model to behave; the code makes it irrelevant whether it does.

## Turn flow

```
                       ┌──────────────────────────────────────────┐
  microphone ─► WAV ─► │ validate_wav_bytes                       │
                       │   RIFF/WAVE header, size, data chunk     │
                       └────────────────┬─────────────────────────┘
                                        ▼
                           ASRRouter   local first, then cloud
                                        ▼
                      LanguageDetector  provider hint → NE-LID → script
                                        ▼
              ┌─────────────────────────────────────────────────────┐
              │            ConversationManager._route                │
              ├─────────────────────────────────────────────────────┤
              │ 1. screen_user_input      injection      → REFUSAL   │
              │ 2. policy.screen_utterance sensitive     → REFUSAL   │
              │ 3. pending confirmation?  explicit yes   → CONFIRMED │
              │ 4. IntentRouter (v4.1)    ≤4 tokens      → COMMAND   │
              │ 5. LLM + ToolRegistry                    → MEMORY /  │
              │                                            CONVERSE  │
              │ 6. DeterministicResponder no model       → FALLBACK  │
              └─────────────────────────────────────────────────────┘
                                        ▼
                            TTSRouter   same language, or nothing
                                        ▼
                             AudioStore  opaque id, expires
```

Steps 1–4 need no network and no model. A refusal or a command never reaches a provider.

## Why the command router is gated at four tokens

The v4.1 router fuzzy-matches against a closed phrase list. That is correct for a
command ("open play") and wrong for a sentence: *"what is the weather today"* scores
highly against the phrase `what is today` and would have opened the schedule screen
instead of answering.

So the command branch only runs on utterances of **four normalised tokens or fewer**.
Every command in the v4.1 regression suite is three tokens or fewer, so no existing
behaviour changed. Longer requests reach the model, which can still open a screen through
the `open_app` tool — which itself resolves to a v4.1 `Action` and passes the same gate.

## Package map

| Package | Responsibility | Provenance |
|---|---|---|
| `intents.py`, `actions.py` | Deterministic command router, action allow-list | **v4.1, logic unchanged** |
| `engine.py` | v4.1 single-utterance command pipeline | **v4.1, unchanged** |
| `asr/providers.py` | Sarvam, OpenAI, IndicConformer, NE-ASR, Whisper | **v4.1, moved verbatim** |
| `audio.py`, `lid.py`, `normalize.py`, `telemetry.py` | Audio quality, NE-LID, text normalisation, JSONL events | **v4.1, unchanged** |
| `safety/` | Policy, validators, injection detection, authorization, confirmation | New (validators extracted from `intents.py`) |
| `language/` | Capability matrix, code normalisation, detection | New |
| `llm/` | Provider abstraction, Gemini/OpenAI/Sarvam/local/mock, routing | New |
| `tts/` | Sarvam/local/mock, language gating, audio store | New |
| `memory/` | Models, repository, service, provenance, lexical retrieval, seed | New |
| `tools/` | Registry, schemas, handlers, weather provider | New |
| `conversation/` | Session state, prompts, deterministic responder, manager | New |
| `offline/` | Connectivity probe, execution mode, health | New |
| `database/` | Connection handling, versioned migrations | New |
| `api/` | FastAPI app, dependencies, routes | v4.1 endpoints preserved |
| `app.py` | Composition root — one wiring for API, CLI and tests | New |
| `pipeline.py` | Voice pipeline orchestration | New |

## Provider abstractions

Every external capability sits behind a protocol, so a vendor can be swapped without
touching the conversation logic:

| Protocol | Implementations |
|---|---|
| `ASRProvider` | `SarvamASR`, `OpenAIASR`, `IndicConformerASR`, `NeASR`, `GenericWhisperASR` |
| `LLMProvider` | `GeminiLLMProvider`, `OpenAILLMProvider`, `SarvamLLMProvider`, `LocalLLMProvider`, `MockLLMProvider` |
| `TTSProvider` | `SarvamTTSProvider`, `LocalTTSProvider`, `MockTTSProvider` |
| `WeatherProvider` | `OpenMeteoWeatherProvider`, `StaticWeatherProvider` |
| `EmbeddingProvider` | *declared, not implemented* — lexical retrieval is used today |

Routers (`LLMRouter`, `TTSRouter`, `ASRRouter`) own selection and fallback. An explicitly
named provider is never silently replaced by another vendor: if it fails, the failure is
reported.

## Tool execution

```
ToolCall (from the model, untrusted)
   → name lookup            unknown            → TOOL_NOT_FOUND
   → pydantic validation    extra/missing/type → TOOL_VALIDATION_ERROR
   → safety screening       sensitive tool or unsafe argument → SAFETY_REFUSAL
   → authorization          wrong user or permission → NOT_AUTHORIZED
   → confirmation gate      controlled action  → pending, awaits an explicit yes
   → handler                                    → ToolResult(executed=True)
```

Every schema sets `extra='forbid'`, so an invented argument is an error rather than a
silently ignored field. Four tools (`change_medication`, `delete_record`, `transfer_money`,
`call_number`) are registered as `SENSITIVE`: they are never advertised to the model and
the registry refuses them before the handler is reached, so an attempt is logged as a
refusal of a known capability rather than an ambiguous unknown name.

## Memory and retrieval

Structured facts are answered with SQL through tools. Unstructured prose (family stories,
caregiver notes) is answered with **lexical BM25-style retrieval** — no embeddings, no
vector database, no download, identical behaviour offline. An `EmbeddingProvider` hook
exists for a future semantic upgrade; nothing pretends to do semantic search today.

Every row carries `source`, `created_by`, `confidence` and `verification_status`. Anything
the assistant writes is stored `unverified` with confidence capped at 0.5, and the read
path passes that flag to the model so it can add a caveat instead of stating it as fact.

Every repository method is scoped by `user_id`. There is no method that *can* read another
user's row, which is why cross-user isolation holds even before authorization runs.

## Execution modes

| Mode | Condition |
|---|---|
| `ONLINE_PRIMARY` | Network reachable and a cloud provider answered |
| `OFFLINE_PRIMARY` | A local LLM answered |
| `DEGRADED` | Deterministic local answers only — commands, memory, routine |
| `ERROR` | Nothing could serve the turn |

`DEGRADED` is a working state, not a failure: family, routine, medicine times, games and
reminders all still answer.

## Concurrency and resources

- SQLite connections are opened per unit of work, not shared across threads. FastAPI runs
  the synchronous handlers in a thread pool, and a shared connection would be a race.
- Uploaded audio goes to a `NamedTemporaryFile` deleted in a `finally` block.
- Generated speech is written to the audio store and pruned on every write, so the
  directory stays bounded without a background task.
- Sessions are capped by count and evicted on idle timeout.
- The connectivity probe is cached (default 20s) so a turn never pays for repeated
  socket timeouts.
