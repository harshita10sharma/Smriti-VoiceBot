# Changelog

All notable changes to the SMRITI VoiceBot service, grouped by theme and
dated from this repository's actual commit history (`git log`). This
project has not yet cut a numbered release; entries below are grouped
under the date each change actually landed, not a version number, since
none has been assigned yet.

## 2026-09-15 — Live AWS deployment, real provider verification, documentation consolidation

- Deployed to AWS for real: EC2 `m7i-flex.large` (`ap-south-1`), persistent 30 GB EBS at
  `/data`, Docker, Caddy with a real Let's Encrypt certificate. Live at
  `https://15-206-144-216.nip.io`. Verified to survive both a container restart and a full
  instance reboot (Docker, Caddy, and the container itself all auto-recover).
- Real, live provider verification against the deployed service: Groq/Qwen conversation,
  Sarvam ASR, Sarvam TTS, and Indic Parler-TTS synthesis (asm) all confirmed working with
  real network calls and real credentials — not mocks.
- Verified live: authenticated text/voice conversation, deterministic safety refusal,
  patient isolation (401/403), asynchronous voice jobs with cancellation and audio
  retrieval, and the full versioned memory-sync contract (apply, stale-revision rejection,
  same-revision-conflict rejection, idempotent identical-revision replay).
- Found and fixed, only by actually deploying: a CUDA-linked `torchaudio` build pulled
  transitively from the wrong package index (failed to import on the CPU-only image);
  Caddy's documented Fedora/RHEL/CentOS install path has no Amazon Linux 2023 target at
  all; a bash parser quirk with an apostrophe inside a `${VAR:?message}` expansion; two
  instances of Git Bash on Windows silently rewriting POSIX-style CLI arguments into
  Windows paths, corrupting two separate AWS CLI calls.
- Rotated `SMRITI_API_KEY`, then `GROQ_API_KEY`/`SARVAM_API_KEY`/`HF_TOKEN` (the latter
  three after a credential accidentally appeared in a development tool transcript),
  redeployed, and re-verified all four working live with the new values.
- 625/625 tests passing; `compileall` clean; `tools/validate_config.py` 0 errors;
  `tools/validate_packs.py` 15/15; all deployment shell scripts `bash -n` clean.
- Documentation consolidation: audited README.md, INTEGRATION_CONTRACT.md,
  API_INTEGRATION.md, HANDOFF.md, BACKEND_APP_DEVELOPER_BACKGROUND.md, CODEBASE_STATUS.md,
  STAGING_READINESS.md, LANGUAGE_SUPPORT.md, EVALUATION.md, and SECURITY.md against the
  actual current code and live deployment; removed or clearly re-labeled stale claims
  (an earlier Windows/Tailscale-Funnel pilot deployment presented as current, outdated
  test counts, "unverified" provider claims now superseded by real live verification, a
  language-validation claim that overstated what "provider execution succeeded" actually
  proves). Historical information was preserved and labeled, not deleted.
- **Remaining work is cross-system, not VoiceBot-owned**: the Backend gateway in
  `Abhayk777/SMRITI` remains unbuilt (confirmed on a fresh clone — planning documents only,
  no VoiceBot-calling code), no Flutter repository is accessible, and physical-device
  acceptance and native-speaker language validation have not been performed. VoiceBot
  service is ready for Backend integration; cross-system integration is not complete.

## 2026-09-14 — AWS release pass: response-language honesty, TTS timeout, deployment plan

- Fixed: a deterministic (non-LLM) response with no translated template for
  the effective language silently reported the originally-requested
  language while the actual text was plain English (e.g. a welcome
  greeting requested in Meiteilon returned English text while still
  reporting `language: "mni"`). Fixed across the welcome, safety-refusal,
  confirmation, action-reply, deterministic-fallback, and voice ASR-error
  paths, which all share the same eng/hin/asm/ben-only template set. The
  requested/session language itself is preserved for later LLM-routed
  turns; only the one templated response's own reported language is
  corrected.
- Fixed: Indic Parler-TTS accepted a `timeout` constructor argument that
  was never actually enforced — `model.generate()` has no built-in
  wall-clock bound, so a stuck or slow generation could block the single
  TTS worker thread indefinitely, stalling the entire voice-job queue.
  Fixed with a bounded wait around the generation call.
- Fixed: the health endpoint's `general_conversation` capability checked a
  hand-maintained provider name list that omitted `groq` entirely — this
  repository's own primary, documented LLM provider — silently
  under-reporting the capability on any deployment without also
  configuring a different listed provider. Fixed to derive from the
  actual configured-provider set.
- Fixed a real Dockerfile bug in a prior pass this project and reconfirmed
  it stayed fixed.
- Re-verified, against current source rather than an older snapshot, a set
  of findings from an external integration review: brand-new-patient
  memory sync, structured medicine-schedule filtering, session ownership
  after cache eviction, audio expiry at read time, and reminder/call
  proposal honesty were all already correct in current source and did not
  need re-fixing.
- Published `docs/AWS_DEPLOYMENT.md` as the authoritative deployment
  target (moved from the earlier Azure evaluation, now kept as historical
  reference) and `deployment/aws/` with reproducible provisioning, deploy,
  smoke-test, backup/restore, and rollback tooling — none of it executed,
  since no AWS credentials are configured in this environment.
- Published `docs/integration/CONTRACT_ACCEPTANCE_MATRIX.md` mapping all
  19 sections of the integration contract to current implementation
  status and owner.

## 2026-09-13 — Zero-signal text no longer overrides an active session's language

- Fixed: a text or voice turn carrying no real language signal (e.g. a
  reply that's just digits or punctuation) was treated as a confident
  English detection and silently overrode an ongoing non-English
  session's actual language for that turn. Fixed in both the text route
  and the voice pipeline to fall back to the session's current language
  in that specific case, using the existing detector's own signal
  (`method == 'default'`) rather than a new heuristic.
- Audited and confirmed already correct, unchanged: automatic per-turn
  text language detection at the API boundary, ASR-driven voice language
  detection and its priority order, per-turn session language updates
  (a patient can switch languages naturally mid-conversation), the LLM
  being explicitly instructed to answer in the effective language, no
  silent TTS-language substitution when a provider can't speak the
  detected language, and deterministic response templates (refusals,
  confirmations) rendering in the effective language.
- Noted, not fixed (separate, broader scope): the deterministic safety
  screen's keyword lists are English-only, so a non-English phrasing of a
  sensitive request (e.g. asking to change a medication dose, in Hindi)
  is not caught by the deterministic gate and falls through to the LLM's
  own judgment instead. Translating every safety keyword list to every
  supported language is a separate piece of work.

## 2026-09-13 — Pre-Azure finalization: Swagger fix, Docker fix, Azure deployment plan

- Fixed: the generated OpenAPI document declared no security scheme and no
  per-operation authentication requirement, because the API-key check is a
  plain header-based dependency FastAPI doesn't auto-detect as
  authentication. Swagger UI showed no "Authorize" button and incorrectly
  implied `x-api-key` was optional on every protected route. Fixed with an
  explicit OpenAPI security scheme declaration; the actual authentication
  mechanism and enforcement are unchanged.
- Fixed: the Dockerfile's `torch`/`transformers`/`parler_tts` installs used
  unquoted `>=` version specifiers inside a shell `RUN` command, which
  `/bin/sh` parses as output redirection rather than a version constraint
  — confirmed by direct reproduction. The version pins were silently
  ignored on every previous build. Fixed by quoting and pinning to the
  exact versions verified working this session.
- Added an additive `action_id` field so a client can correlate a specific
  action proposal with its later confirmation turn.
- Re-verified, with real credentials and real network calls: Groq LLM,
  Sarvam ASR, Sarvam TTS, and Indic Parler-TTS (Assamese) all confirmed
  working, including a real measured memory footprint for Indic
  Parler-TTS to size Azure compute correctly rather than guessing.
- Published `docs/AZURE_DEPLOYMENT.md` as the authoritative Azure
  deployment reference, and the full Backend/Flutter integration
  documentation package (`docs/VOICEBOT_INTEGRATION_GUIDE.md`,
  `docs/BACKEND_VOICEBOT_INTEGRATION.md`,
  `docs/FLUTTER_VOICEBOT_INTEGRATION.md`, `docs/RELEASE_ACCEPTANCE.md`,
  and machine-readable OpenAPI/schema/language-matrix/error-catalog
  exports generated directly from the running application).

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
