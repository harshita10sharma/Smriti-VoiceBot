# Codebase status

**Version:** 5.0.0 · **Date:** 2026-09-05

Status of every major component, stated precisely. Where something is unverified, this
document says so rather than implying it works.

## Legend

| Status | Meaning |
|---|---|
| **WORKING** | Implemented, executed here, covered by tests |
| **IMPLEMENTED, UNVERIFIED** | Real code against current documentation, but never executed against the real service |
| **NOT BUNDLED** | Interface exists; the artefact (model weights, server) is the operator's to supply |
| **PRESERVED** | v4.1 code carried across unchanged |

---

## Verification performed

| Check | Result |
|---|---|
| `python -m compileall -q .` | clean |
| `pytest -q` | **280 passed, 0 failed** |
| v4.1 regression cases (10 safe, 7 unsafe) | all correct, before **and** after migration |
| Safety and red-team suite | **156 assertions pass** |
| Live `uvicorn` boot | started, `Application startup complete` |
| Live HTTP endpoints via `curl` | health, languages, languages/{code}, conversation, tools, auth |
| Voice pipeline end to end | real WAV → pipeline → real WAV out, audio fetched over HTTP |
| Upload validation | empty 400, invalid 415, truncated 415, wrong type 415, oversized 413 |
| `tools/validate_config.py` | 0 errors, 5 warnings (all "no credential configured") |
| `tools/smoke_test.py` | 13 local checks pass; provider checks correctly report SKIP/FAIL |
| Git secret audit | no `.env`, `.wav`, `.db`, `.log`, no key material in any tracked file |

Two real bugs were found by this verification and fixed:

1. `/v1/health` reported `voice_output: true` when only an unconfigured local TTS object
   could be constructed. It now reports true only when a provider is configured **and**
   can speak a configured language.
2. `VoicePipeline.process` passed `language_confidence` twice, so **every** voice request
   raised `TypeError`. The voice endpoint's success path had no test; it now has ten.

---

## Component status

### Safety — **WORKING**

| Part | File | Notes |
|---|---|---|
| Unsafe-utterance veto | `safety/validators.py` | v4.1 logic, extracted so one implementation serves both paths |
| Sensitive-category policy | `safety/policy.py` + `config/safety.json` | 6 categories, config-driven |
| Prompt-injection detection | `safety/prompt_injection.py` | 7 pattern families |
| Authorization | `safety/authorization.py` | Per-user, fails closed |
| Confirmation | `safety/confirmation.py` | Ambiguity never counts as consent |
| Action allow-list | `actions.py` | **PRESERVED** from v4.1 |

Screening runs **before** the model. A refusal never reaches a provider.

### Conversation — **WORKING** (never run against a real model)

`conversation/manager.py` routes every turn through: injection screen → safety screen →
pending confirmation → v4.1 command router → LLM with tools → deterministic fallback.
Session state, bounded history, language following and confirmation are all covered by tests.
The LLM branch has only ever run against `MockLLMProvider`.

### Personal memory — **WORKING**

`memory/` — SQLite, 16 tables, versioned migrations, provenance on every fact, every query
scoped by `user_id`. Cross-user isolation is tested with two real users.

### RAG — **WORKING** (lexical, by design)

`memory/rag.py` — BM25-style ranking with ASCII-only stemming. No embeddings, no vector
database, no download; identical behaviour offline. An `EmbeddingProvider` protocol is
declared for a future semantic upgrade and is **not** implemented — nothing pretends to do
semantic search.

### Tools — **WORKING**

21 registered: 13 read-only, 4 controlled actions, 4 sensitive-and-blocked. 17 advertised
to the model. Every schema uses `extra='forbid'`.

### ASR — **PRESERVED / PARTIALLY VERIFIED (updated 2026-09-05)**

Provider classes are the v4.1 implementations, moved verbatim. `asr/router.py` is new.
**Cloud ASR (Sarvam/OpenAI) has still never been called** — no credential is configured
in any environment this project has run in.

**Local ASR was for real, on a later session with working network egress.** This
surfaced and fixed a real bug: `IndicConformerASR.warmup()` used to pre-create its model
directory, which tricked the installed `onnx-asr` release into skipping the Hugging Face
download every time (see `EVALUATION.md` addendum for the full story). After the fix, real
downloads + real ONNX inference succeeded for **Bodo, Manipuri, Nepali and Hindi**
(132 MB each, genuine `ai4bharat` IndicConformer weights via the `OpenVoiceOS` ONNX
export). **Assamese's configured model id turned out to be an empty placeholder repo on
Hugging Face** — a real, confirmed upstream data gap, not a bug in this codebase — so its
status was downgraded from `validated_local` to `benchmark_only` in
`language_packs/ner_languages.json` pending a real model. No recorded human speech was
available to measure transcription accuracy (WER); what was verified is that the
download → load → inference pipeline is genuinely wired and running on this machine for
brx/mni/npi/hin.

### LLM — **GROQ/QWEN DEPLOYMENT PATH VERIFIED**

| Provider | Endpoint | Tool calling | Verified |
|---|---|---|---|
| Groq | `chat.completions` | yes | configured deployment path; mocked regression coverage |
| Gemini | `v1beta/models/{model}:generateContent` | yes | no |
| OpenAI | `v1/chat/completions` | yes | no |
| Sarvam | `api.sarvam.ai/v1/chat/completions` | **unverified — off by default** | no |
| Local | any OpenAI-compatible server | yes | **NOT BUNDLED** |
| Mock | in-process | yes | used by all tests |

The deployment selects Groq with `qwen/qwen3.8-27b`. Request/response mapping, tool-call
parsing, strict explicit routing, retry configuration, and sanitized errors are tested with
synthetic payloads. Other providers remain available when explicitly selected.

### TTS — **IMPLEMENTED, UNVERIFIED** (one confirmed gap, one confirmed external blocker)

Sarvam Bulbul provider written against current documentation; no live call made.
**Confirmed limitation:** Bulbul does not document Assamese, Bodo, Manipuri, Nepali or any
Northeast language. The router returns `NO_TTS_PROVIDER_SUPPORTS_LANGUAGE` rather than
substituting another language — tested.

**Indic Parler-TTS** — the intended local voice for exactly those four languages — is now
**validated end-to-end against the real gated model** (as of 2026-09-05, with a granted
`HF_TOKEN`). Dependencies (`torch`, `transformers`, `parler-tts`) install and import
correctly (Microsoft Visual C++ Redistributable required on the Windows host; see
`requirements.txt`). With the token present, `ai4bharat/indic-parler-tts` (~3.7 GB)
authenticated and downloaded successfully. For `asm`, `brx`, `mni` and `npi`:
`IndicParlerTTSProvider` loaded the real model, generated real non-silent WAV audio via
`model.generate(...)`, and returned a `TTSResult` with the exact requested language (no
substitution); `TTSRouter.synthesize` selected the `indic_parler` provider and stored the
audio via `AudioStore`; the pipeline's `turn_language` propagation
(`detected → response → TTS`) was confirmed to carry the same code through unchanged for
all four languages. The mocked unit tests in `tests/unit/test_tts_indic_parler.py` remain
the fast/offline regression suite; this real-model run is the one-time credential-gated
confirmation that the mocks accurately reflect the official `parler-tts` API shape.

### Language handling — **WORKING** (detection partially verified)

15 packs. Capability matrix derives status from measured evidence; `config/language_validation.json`
is empty, so **0 languages are SUPPORTED**. Script detection is tested; NE-LID is untested
(package not installed here). Assamese/Bengali separation needs an Assamese-only letter or NE-LID.

### Online/offline — **WORKING** (no local model tested)

Connectivity probe, four execution modes, deterministic fallback covering family, meals,
medicine, routine, visitors, reminders and games in English, Hindi, Assamese and Bengali.
No local ASR/LLM/TTS has been run, so no RAM, CPU or latency figures exist for them.

### API — **WORKING**

8 endpoints, all verified over live HTTP. Auth fails closed. Audio never enters JSON or logs.

### CLI — **WORKING**

`--list-languages`, `--health`, `--file`, `--record`, `--language` (v4.1, unchanged) plus
`--test-config`, `--seed-demo`, `--chat`.

---

## What is preserved from v4.1

| Module | Change |
|---|---|
| `intents.py` | Only the shared validator import; logic identical |
| `actions.py`, `engine.py`, `audio.py`, `lid.py`, `normalize.py`, `telemetry.py`, `intent_semantic.py`, `responder.py` | none |
| `asr.py` | moved to `asr/providers.py`, re-exported |
| `api.py` | became the `api/` package; `from smriti_voice.api import app` still works |
| `language_packs/`, `config/settings.json`, `tools/*.py` | none |
| `tests/test_core.py` | moved to `tests/legacy/test_core_v41.py`; root path and API-key fixture only |

Two deliberate behaviour changes, both safety improvements, both documented:

1. The command router runs only on utterances of ≤4 tokens (natural questions were being
   hijacked). No v4.1 command is affected — all are ≤3 tokens.
2. Placing a call requires spoken confirmation **in the conversational path**. `/v1/command`
   is unchanged.

---

## Cannot be tested here, and why

| Item | Reason |
|---|---|
| Sarvam ASR / TTS, Gemini, OpenAI, Open-Meteo | Environment egress policy blocks these hosts (403 on CONNECT). A real credential was present and the call still failed at the network layer. |
| Local ASR (IndicConformer, NE-ASR) | Models not provisioned; `onnx-asr`/`transformers`/`torch` not installed. |
| Local LLM / local TTS | Not bundled by design. Requires an operator-supplied server. |
| NE-LID accuracy | `ne-lid` package not installed. |
| WER / CER / real latency | Requires real speech and reachable providers. |
| Assamese voice output | **UNSUPPORTED** by the configured provider — a fact, not an untested item. |
| Microphone capture / playback | No audio hardware. |
| Multi-worker rate limiting | In-memory limiter is per-process. |

---

## Honest bottom line

The safety-critical layer is complete, deterministic and adversarially tested: the model
cannot execute anything, and every refusal holds.

Every **cloud provider path** and every **local model path** is either mocked or untested.
This system is not ready for a production deployment or a claim of language support. It is
ready to be pointed at real credentials on a machine with network access, at which point
`tools/smoke_test.py` and `tools/evaluate_languages.py` will produce the measurements that
`EVALUATION.md` currently records as absent.
