# Changelog

All notable changes to the SMRITI VoiceBot service, grouped by theme and
dated from this repository's actual commit history (`git log`). This
project has not yet cut a numbered release; entries below are grouped
under the date each change actually landed, not a version number, since
none has been assigned yet.

## 2026-09-13 — Integration hardening and real-provider fixes

- Fixed: the voice endpoint's ASR-failure/no-speech error path returned an
  empty `session_id` instead of a real, continuable one, and did not catch
  a cross-patient `session_id` the way the text endpoint already did
  (surfaced as an unhandled `500` instead of a clean `403`).
- Fixed: the default Sarvam Bulbul TTS model and speaker had been
  deprecated by the provider, so every real voice-output request failed
  with `TTS_UNAVAILABLE` regardless of credentials; moved to the current
  model and a speaker verified compatible with it.
- Fixed: an OpenAI ASR fallback path could hint Meiteilon transcription
  requests with the ISO-639-1 code for Mongolian instead of omitting the
  hint as intended for languages without a real two-letter code.
- Fixed: a caller-supplied language alias (e.g. a caregiver backend's own
  `hi`/`as` codes) was validated but never normalized to the internal
  canonical code before template lookup, so the welcome greeting and
  voice-error text silently fell back to English for a real, supported
  language.
- Fixed: two requests sharing the same `session_id` (a rapid double-tap, a
  client retry racing the original turn) could race around the
  read-mutate-persist cycle for pending confirmations, conversation
  history, and session language/subject state, risking a lost update or a
  controlled action executing twice. Added per-session serialization.
- Fixed: the shared in-memory SQLite connection used by the test suite had
  no serialization across threads calling commit/rollback concurrently — a
  real, reproducible source of intermittent `InterfaceError`/
  `OperationalError` failures under concurrent load.
- Fixed: `requirements.txt` was missing `tzdata` entirely — every
  per-patient IANA timezone calculation depends on it, and a genuinely
  clean install (no ambient system/global installation of it) fails with
  `ZoneInfoNotFoundError`.
- Hardened: dependency pinning moved from open-ended minimum version
  ranges (`>=`) to exact pins for every direct runtime dependency,
  verified against the full test suite in a clean virtual environment, not
  just the existing development machine.
- Added regression coverage for: prompt injection carried through
  synchronized caregiver memory (verifying it stays data, never an
  instruction, at both the sanitization and deterministic-authorization
  layers), concurrent same-session turns (normal turns, confirmations,
  cross-patient hijack attempts, expiring confirmations, simulated
  restart), and credential rotation (memory and session ownership survive
  a credential change; the old credential is rejected immediately).

## 2026-09-13 — Contract publication and revocation

- Published `INTEGRATION_CONTRACT.md` as the verified, single starting
  point for the Backend and Flutter teams, cross-referencing `HANDOFF.md`,
  `SECURITY.md`, and `LANGUAGE_SUPPORT.md`.
- Closed an `external_id` collision gap across patients: reusing one for a
  different owner now returns a clean `409` instead of an unhandled error.
- Added an end-to-end contract test harness exercising the full
  Backend-to-Flutter chain against real request/response shapes.
- Added patient revocation (disable/re-enable a patient via
  `POST /v1/memory/sync`'s `active` field) with no separate admin endpoint
  needed.
- Added a staging configuration profile and a remote smoke-test script,
  and published integration contract fixtures for cross-repository
  testing.

## 2026-09-11/13 — Persistent sessions, memory versioning, voice job reliability

- Moved conversation session ownership checks onto persistent SQLite
  storage, surviving a process restart instead of depending on in-memory
  state.
- Closed a gap where a stored action confirmation could be answered after
  the process restarted without being re-validated.
- Added per-patient IANA timezone handling so time-sensitive answers
  ("what medicine tonight") resolve in the patient's own timezone, not a
  single service-wide default.
- Extended caregiver-memory synchronization with opt-in revisioning
  (`source_revision`/`schema_version`): stale revisions are rejected,
  identical repeats are a no-op, conflicting same-revision content is
  rejected, and the applied revision is reported back.
- Added structured medicine scheduling (`chosen_time_min`/
  `window_start_min`/`window_end_min`/`days_of_week`, non-wrapping window
  enforced at validation) and stable external IDs for caregiver-synced
  people/medicines/routines.
- Added voice-job cancellation with race-safety against the synthesis
  worker, a processing deadline enforced at read time, and an explicit
  maximum audio duration.
- Audited and corrected the language capability matrix against actual
  provider support rather than configuration presence alone.
- Hardened integration boundaries: authorization checked independently of
  whether a patient row exists yet, and capability reporting made honest
  about what is actually validated versus merely configured.

## 2026-09-06/08 — Async voice jobs, memory sync, multi-user auth

- Made the voice endpoint asynchronous: text response returns immediately,
  speech synthesis runs as a pollable background job instead of blocking
  the request.
- Added caregiver memory synchronization (family members, medicines,
  daily routines) as a full-snapshot, transactional replace.
- Added multi-user API key authentication (`SMRITI_API_KEYS`), so one
  backend credential can be scoped to one or many authorized patients
  without a code change per patient.
- Routed general conversation through Groq; fixed Gemini tool-calling
  continuity across multiple rounds in the same turn.
- Added deployment configuration for a hosted platform and documented the
  API for backend integration, including VoiceBot API key generation.

## 2026-09-04/05 — Initial implementation

- Initial implementation of the ASR pipeline, language detection and
  capability routing, deterministic command engine and safety validators,
  LLM provider routing, personal-memory storage, the tool registry and
  built-in tools, the conversation manager, TTS providers (including
  Indic Parler-TTS), the voice pipeline, the HTTP API and authentication,
  the integration/regression test suite, and initial architecture/API/
  deployment documentation.
