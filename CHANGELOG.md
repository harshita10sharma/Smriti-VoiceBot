# Changelog

All notable changes to the SMRITI VoiceBot service, grouped by theme and
dated from this repository's actual commit history (`git log`). This
project has not yet cut a numbered release; entries below are grouped
under the date each change actually landed, not a version number, since
none has been assigned yet.

## 2026-09-16 (later same day) — Integration-documentation audit: 12 real bugs found and fixed

A full cross-document audit of every doc a Backend/Flutter developer would need, checked
against each other and against actual source code (not each other's claims). Fixed:

- **Wrong idempotency header name in the document declared authoritative**:
  `INTEGRATION_CONTRACT.md` said `Idempotency-Key`; the real header is `X-Idempotency-Key`.
  Following the doc literally would have silently disabled idempotency protection — no
  error, just a retried side-effecting call double-executing. Fixed there and in
  `integration/fixtures/voice_request.md`.
- **Wrong field name in the Backend field-mapping table**: `docs/BACKEND_VOICEBOT_INTEGRATION.md`
  mapped `people[].relationship` to `family_members[].relation`; the real field is
  `relationship`. Since the model forbids unknown fields, following the table as written
  would 422 on every sync.
- **The `id` field for dynamic keys was undocumented in the authoritative auth docs**
  (`SECURITY.md`, `INTEGRATION_CONTRACT.md` §1) despite being the thing that makes credential
  rotation safe; `SECURITY.md` additionally made a blanket-false claim that rotation "loses
  no patient data" for a dynamic key without `id`. Both fixed.
- **Two docs falsely claimed dynamic provisioning was still unbuilt** (`CONTRACT_ACCEPTANCE_MATRIX.md`,
  `VOICEBOT_INTEGRATION_GUIDE.md`), contradicting the code and `PROVISIONING_DESIGN.md`'s own
  status. `BACKEND_APP_DEVELOPER_BACKGROUND.md` didn't mention dynamic mode at all and claimed
  multi-user was "not active in the current deployment" — false; it is what's actually
  deployed. All fixed.
- **`docs/integration/language_matrix.json`'s `status` field never matched the real API**
  (`"LanguageStatus.NOT_YET_TESTED"` instead of `"NOT_YET_TESTED"` — a classic Python
  `str(Enum)` gotcha) for all 15 languages, and was missing 7 real response fields. Root
  cause: it was hand-maintained despite claiming to be generated. Fixed permanently by adding
  `tools/generate_language_matrix.py`, which derives the file directly from the same
  `model_dump(mode='json')` call the live endpoint uses — it cannot drift again as long as
  it's re-run after language changes.
- **`LANGUAGE_SUPPORT.md` falsely claimed Assamese offline ASR works** (`yes`), and separately
  claimed to be auto-generated when it was hand-maintained prose; both false claims corrected,
  and its "known limitations" section for Assamese now includes the benchmark-only caveat
  that was missing.
- **Three real job `error_code` values were undocumented**: `TTS_WORKER_ERROR`,
  `CANCELLED_BY_CLIENT`, `EMPTY_RESPONSE_TEXT`. `error_catalog.json` additionally claimed
  `error_code` only appears on a terminal *failed* job — false, it also appears on
  *cancelled* jobs. `ASR_UNAVAILABLE`/`NO_SPEECH_DETECTED` (real voice-turn error codes) were
  missing from `INTEGRATION_CONTRACT.md`'s own error table. All fixed in both files.
- **A real, silent validation gap, not just a doc mismatch**: `chosen_time_min` was never
  actually checked against its own `window_start_min`/`window_end_min` in
  `smriti_voice/schemas.py`, despite two docs already claiming it was enforced. Fixed in code
  (`MemorySyncMedicine`'s validator now rejects it), with two new regression tests.
- Narrower fixes: `API.md` didn't document the async voice-job architecture at all (described
  `/v1/conversation/voice`'s response as if `audio_id` were populated immediately — it's
  always `null` there); missing `WELCOME` turn kind; single-key-only auth section. Truncated
  6-of-15-language tables in `INTEGRATION_CONTRACT.md`/`HANDOFF.md` now say so explicitly and
  point to the full matrix. `job_status` (uppercase) vs job-polling `status` (lowercase)
  casing difference documented. `user_id` regex documented as Unicode-aware, not strictly
  ASCII. A dead-code fourth error envelope (`SmritiError` handler, unreachable on every
  current path) documented rather than left silently contradicting a claim that no such
  envelope exists. Added explicit client-timeout guidance (≥65s) to
  `docs/BACKEND_VOICEBOT_INTEGRATION.md` and `HANDOFF.md` given Groq's measured worst-case
  latency. Corrected stale "625 passing"/"588+ tests" counts to the current 655 across
  `README.md`, `CODEBASE_STATUS.md`, `EVALUATION.md`, `TESTING.md`,
  `docs/integration/CONTRACT_ACCEPTANCE_MATRIX.md`.
- 655/655 tests passing (653 prior + 2 new regression tests for the medicine-window fix).

## 2026-09-16 — Dynamic multi-patient provisioning, rotation-safe grants, live reliability tuning

- Added dynamic multi-patient authorization for backend API keys (`cefcf53`): a
  `SMRITI_API_KEYS` credential can now be configured as `{"dynamic": true, ...}`, growing its
  authorized patient set automatically the first time it successfully syncs a given
  `user_id` via `POST /v1/memory/sync` — no env-var edit or restart needed to add a patient.
  It never becomes a wildcard: an unsynced `user_id` is still `403`, exactly like a
  fixed-list key. Backed by a new `backend_key_grants` table (migration 7).
- Added test coverage for simultaneous multi-patient conversations, and session/memory/job/
  audio/cancellation isolation, and independent disable/re-enable across patients (`e3ab93c`).
- Documented the mechanism as implemented (previously described as a future design) in
  `PROVISIONING_DESIGN.md`, `docs/BACKEND_VOICEBOT_INTEGRATION.md`, and
  `INTEGRATION_CONTRACT.md` (`b1afb46`).
- Fixed: `/v1/health`'s `authentication_configured` field was blind to `SMRITI_API_KEYS`,
  reporting authentication as unconfigured even in a correctly configured multi-user
  deployment (`1eb0514`).
- Fixed: a dynamic key's grants were scoped to the SHA-256 hash of its own secret value, so
  rotating that secret (a routine security practice) silently orphaned every patient already
  granted to it, each requiring one repeat sync to re-authorize. Fixed by adding an optional,
  operator-set stable `id` field — grants are now scoped to `id` when present, surviving
  secret rotation with zero manual steps; two keys sharing an `id` are rejected at config
  load. Found live, by rotating a production secret and observing real patients return `403`
  after having worked correctly moments before; fix verified live by rotating a second time
  and confirming zero re-granting was needed (`c37f211`).
- Full live re-sweep of the deployed service (~30 checks: auth matrix, safety refusal,
  command routing, multi-user provisioning/isolation, simultaneous-patient text,
  cross-patient session-swap rejection, disable/re-enable independence, memory-sync
  revisioning, prompt-injection resistance, real voice round trip, cancellation,
  language-endpoint honesty) passed cleanly once test-harness contention and script bugs in
  the sweep tooling itself were ruled out — no VoiceBot defect found.
- Fixed: production's `SMRITI_MAX_RETRIES` was unset (defaulting to `2`), giving Groq's own
  SDK retry loop a worst-case wall-clock of ~90s (3 attempts × the 30s per-attempt timeout)
  during provider latency spikes. Set explicitly to `1` in
  `deployment/aws/.env.production.local` and redeployed (container restart only), halving the
  worst case to ~60s without reducing single-attempt reliability. Groq's own latency variance
  (confirmed up to ~35s on a single, non-retried call) remains a property of its free/shared
  tier, not something fixable in this codebase.
- 653/653 tests passing.

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
