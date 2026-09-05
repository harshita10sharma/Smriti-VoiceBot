# SMRITI VoiceBot — Audit of the existing v4.1 implementation

Audit performed before any code was modified. Every statement below was verified by
reading the source and by executing the existing test suite on commit `4f6c0c1`
("Initial v4.1 VoiceBot baseline").

## 1. Baseline test run (before any change)

```
$ python -m pytest -q
9 passed, 2 warnings in 0.36s
```

All nine pre-existing tests in `tests/test_core.py` passed unmodified. They are
preserved verbatim in `tests/legacy/test_core_v41.py` and continue to pass after
the migration.

## 2. Inventory of existing modules

| Module | Lines | Purpose | Disposition |
|---|---|---|---|
| `smriti_voice/intents.py` | 85 | Deterministic fuzzy command router (RapidFuzz), threshold + ambiguity margin, unsafe-utterance veto | **PRESERVED** — safety-critical action layer, logic unchanged |
| `smriti_voice/actions.py` | 13 | `Action` enum + `SafeActionGate` allow-list | **PRESERVED** verbatim |
| `smriti_voice/asr.py` | 118 | Sarvam / OpenAI / IndicConformer / NE-ASR / Whisper providers, `ProviderFactory` | **PRESERVED** — moved verbatim into `smriti_voice/asr/providers.py`, re-exported from `smriti_voice.asr` |
| `smriti_voice/engine.py` | 122 | v4.1 command pipeline (ASR → LID → intent → gate → telemetry) | **PRESERVED** — still the command path, still reachable at `/v1/command` |
| `smriti_voice/config.py` | 37 | `Settings`, `LanguagePack`, `LanguageRegistry` | **PRESERVED** and extended with env-driven `AppConfig` |
| `smriti_voice/normalize.py` | 5 | NFKC + casefold + punctuation strip | **PRESERVED** verbatim, now also used by the safety layer |
| `smriti_voice/lid.py` | 15 | NE-LID wrapper for 11 NER languages | **PRESERVED** verbatim |
| `smriti_voice/audio.py` | 41 | Quality assessment, trim/VAD-lite, resample to 16 kHz, push-to-talk record | **PRESERVED** verbatim |
| `smriti_voice/telemetry.py` | 11 | JSONL event log, strips audio keys | **PRESERVED**, extended with new fields |
| `smriti_voice/intent_semantic.py` | 63 | OpenAI Responses-API constrained action classifier | **PRESERVED** verbatim |
| `smriti_voice/responder.py` | 9 | Local pre-recorded WAV playback | **PRESERVED** verbatim |
| `smriti_voice/api.py` | 58 | FastAPI app: `/v1/health`, `/v1/languages`, `/v1/command` | **MIGRATED** into the `smriti_voice/api/` package; `from smriti_voice.api import app` still works and `/v1/command` keeps its exact contract |
| `language_packs/*.json` | 15 packs | Per-language phrase packs + `ner_languages.json` registry | **PRESERVED** — still the single source of language truth |
| `config/settings.json` | 24 | Runtime settings | **PRESERVED**, keys added |
| `tools/*.py` | 3 scripts | Pack validation, model provisioning, threshold calibration | **PRESERVED** verbatim |
| `main.py` | 25 | CLI | **EXTENDED** (existing flags unchanged) |

Nothing from v4.1 was deleted.

## 3. Safety behaviour that already worked

The v4.1 router implements a *veto before match*: `IntentRouter._has_conflicting_utterance()`
runs before fuzzy scoring, so a mutating verb next to a protected noun can never
reach the action allow-list, no matter how well it fuzzy-matches a safe phrase.

Verified on the unmodified v4.1 code:

| Utterance | v4.1 result |
|---|---|
| `open play` | accepted `OPEN_PLAY` (100.0) |
| `play` | accepted `OPEN_PLAY` (100.0) |
| `show my family` | accepted `OPEN_MY_PEOPLE` (95.5) |
| `show my people` | accepted `OPEN_MY_PEOPLE` (100.0) |
| `what's today` | accepted `OPEN_TODAY` (95.5) |
| `help` | accepted `HELP` (100.0) |
| `stop` | accepted `STOP` (100.0) |
| `medicine` | accepted `OPEN_MEDICINE` (100.0) |
| `open medicine` | accepted `OPEN_MEDICINE` (100.0) |
| `call Bina` | accepted `CALL_BINA` (100.0) |
| `delete my medicine` | rejected `NO_ACTION` / `unsafe_conflicting_request` |
| `change my dosage` | rejected `NO_ACTION` / `unsafe_conflicting_request` |
| `remove my medicine` | rejected `NO_ACTION` / `unsafe_conflicting_request` |
| `transfer money` | rejected `NO_ACTION` / `unsafe_conflicting_request` |
| `call 9876543210` | rejected `NO_ACTION` / `unsafe_conflicting_request` |
| `delete my family` | rejected `NO_ACTION` / `unsafe_conflicting_request` |
| `change my game` | rejected `NO_ACTION` / `unsafe_conflicting_request` |

All 17 cases behaved correctly **before** the migration. They are now pinned by
`tests/safety/test_v41_regression.py`, which was written and run against the
unmodified v4.1 router before any safety code was touched.

Other pre-existing safety properties preserved:

- `SafeActionGate` maps anything outside the eight-value allow-list to `NO_ACTION`.
- Empty phrase packs fail closed (`commands_mni.json` has no phrases → every
  utterance rejected) — never a silent fallback to another language's phrases.
- `Telemetry.log()` drops `audio`/`raw_audio` keys; `retain_raw_audio` defaults false.
- The API rejects non-WAV, empty and truncated uploads before touching the engine.
- `HF_HUB_OFFLINE=1` is forced unless `SMRITI_PROVISIONING=1`, so the production
  runtime never downloads a model.

## 4. Defects found during the audit

| # | Finding | Severity | Action taken |
|---|---|---|---|
| A1 | `smriti_voice/__init__.py` declared `__version__='3.0.0'` while `pyproject.toml` said `4.1.0` | Low | Both now read `5.0.0` from one place |
| A2 | `engine.py` line ~112: the Sarvam translate-retry branch checks `isinstance(self._asr.get(lang), SarvamASR)`, but `_asr` is only populated by the **local** provider path, so the retry was dead code for online turns | Medium (dead fallback) | Fixed to use the provider object actually used for the turn; covered by a new test |
| A3 | `IntentRouter.classify()` normalised the text and then passed the **raw** text to `candidates()`, which normalised again — harmless but double work | Trivial | Left as-is (behaviour-identical, production-tested) |
| A4 | `auth()` allowed unauthenticated access when `SMRITI_ALLOW_UNAUTHENTICATED=1` | Accepted risk | Preserved, but now logged loudly at startup and documented in SECURITY.md |
| A5 | `intents.py` owned the unsafe-verb tables, so a second consumer (the new conversational layer) would have had to duplicate them | Medium (drift risk) | Tables and `has_conflicting_utterance()` moved to `smriti_voice/safety/validators.py`; `intents.py` imports them. Byte-for-byte same logic, guarded by the regression test above |
| A6 | `tools/calibrate_thresholds.py` ignores its own `th` loop variable when scoring | Low | Fixed (threshold is now applied per iteration) |
| A7 | No `requirements` pin for `pydantic`; it arrived transitively via FastAPI | Low | Added explicitly |

## 5. Gaps relative to the new specification

v4.1 is a **closed-vocabulary command router**. It has no LLM turn, no personal
memory, no tools, no TTS, no conversation state, no offline/online policy object.
Those are the *additions* in v5.0. The command router is not replaced by them —
it runs first on every turn and keeps final authority over actions.

## 6. Migration principle applied

```
v4.1 command path  →  preserved unchanged, now one branch of the turn router
new conversational path  →  only reached when the command router does NOT accept
action execution  →  still only via SafeActionGate's eight-value allow-list
```

The LLM can *propose*; only the deterministic layer *authorises*.
