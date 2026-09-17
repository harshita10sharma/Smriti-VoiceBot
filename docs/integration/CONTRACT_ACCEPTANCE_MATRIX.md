# 19-Section Contract Acceptance Matrix

Maps every section of "SMRITI VoiceBot: Integration Changes and Shared Contract" (the
authoritative integration contract) to its current implementation status, owner, and
evidence. Statuses: **VOICEBOT PASS**, **BACKEND EXTERNAL**, **FLUTTER EXTERNAL**,
**JOINT EXTERNAL**.

## 1. Architecture and ownership
**VOICEBOT PASS.** VoiceBot remains a separate FastAPI service; not moved into Supabase
Edge Functions; no unrestricted database access. Provider/API credentials never reach
Flutter or the browser — verified (`tests/unit/test_privacy_data_minimization.py`).

## 2. Patient provisioning and authorization
**VOICEBOT PASS.** Idempotent auto-provisioning on first contact; `user_id` accepts a
Supabase UUID directly (no translation layer needed); authorization enforced independently
of provisioning via `SMRITI_API_KEYS`; disable/re-enable via `active`; credential rotation
verified to preserve memory (`tests/integration/test_credential_rotation.py`); cross-patient
isolation verified across session/job/audio/memory. Scalable-beyond-env-var provisioning
(a "dynamic" `SMRITI_API_KEYS` credential whose authorized set grows automatically on first
sync per patient, with a stable `id` field surviving credential rotation) is **implemented**,
not merely designed — `tests/integration/test_dynamic_backend_key_provisioning.py`,
`PROVISIONING_DESIGN.md`, live-verified on the deployed instance (2026-09-16).

## 3. Persistent conversation ownership
**VOICEBOT PASS.** SQLite-backed session ownership, restart-surviving
(`tests/integration/test_persistent_sessions.py`); consistent 403 across text/voice;
per-session concurrency locking added and verified this project
(`tests/integration/test_concurrent_sessions.py`, confirmed to actually catch the race by
reverting the fix and observing failure); confirmations bound to session/action/expiry, no
implicit approval.

## 4. Versioned caregiver-memory synchronization
**VOICEBOT PASS.** Full snapshot, transactional, revision hash covers every field including
patient-context-only changes (`tests/integration/test_memory_sync_versioning.py`, 17 tests).
A Backend-team review's "new allow-listed identity fails nonempty sync" finding does not
reproduce against current source (verified directly this session, real HTTP, `200 applied`).

## 5. Structured medicine/time handling
**VOICEBOT PASS.** `chosen_time_min`/`window_start_min`/`window_end_min` (0–1439),
comma-separated ISO weekdays (Monday=1), non-wrapping window enforced at validation, per-
patient IANA timezone actually affects "tonight"/"tomorrow" resolution
(`tests/unit/test_per_patient_timezone.py`). Deterministic weekday/time filtering confirmed
present in `memory/service.py::medicines()` — a Backend-team review's "schedule written into
instructions, structured time empty" finding does not reproduce against current source.

## 6. Reliable asynchronous voice processing
**VOICEBOT PASS**, with one real fix this session. Bounded queue, restart recovery
(`recover_stale_jobs()`), cancellation race-safety, duplicate protection via idempotency,
audio expiry enforced at read time (not just write time — a Backend-team review's finding to
the contrary does not reproduce against current source). **Real fix**: Indic Parler-TTS's
`timeout` parameter was accepted but never enforced — a stuck `model.generate()` call could
block the single TTS worker indefinitely; fixed with a bounded wait
(`tests/unit/test_tts_indic_parler.py::test_synthesize_raises_provider_timeout_instead_of_hanging_forever`).
Whole-turn latency: real measurements (LLM ~814ms, ASR ~845ms) remain well inside any
reasonable gateway timeout; the existing async-TTS-only architecture is retained, not
redesigned, per measurement rather than guesswork.

## 7. Language capabilities and quality
**VOICEBOT PASS**, with one real fix this session. Per-turn automatic detection (text and
voice), correct priority order, Meiteilon never mapped to Mongolian (fixed in an earlier
project pass, `tests/unit/test_asr_openai_language_hints.py`), no silent TTS-language
substitution. **Real fix**: a deterministic (non-LLM) response with no translated template
for the effective language silently reported the *requested* language while the actual text
was English (e.g. `language: "mni"` with English welcome text) — fixed across
welcome/refusal/confirmation/action-reply/ASR-error paths
(`tests/integration/test_response_language_honesty.py`, 8 tests). Native-speaker validation
remains explicitly unvalidated (`validated: false` for every language) — **JOINT EXTERNAL**,
requires human speakers.

## 8. Structured actions and execution acknowledgements
**VOICEBOT PASS.** `action_id` (added this project) correlates a proposal with its
resolution; `action_accepted` explicitly documented as not proof of a real side effect;
unknown actions fail closed (`test_unknown_tool_name_fails_closed`); navigation actions map
to `open_app`; opening Games never fabricates a session.

## 9. Controlled calling
**VOICEBOT PASS (foundation only) / JOINT EXTERNAL (execution).** Opaque trusted-contact
resolution, no model-generated phone numbers accepted, confirmation required. No real
telephony executor exists in this repository, by design — calling stays disabled pending
Backend/Flutter building one. `phone_available` confirmed inert for calling-trust purposes
(cannot enable calling by itself) — a Backend-team review's concern already correctly
addressed by the existing schema shape.

## 10. Conversational reminders
**VOICEBOT PASS (foundation only) / JOINT EXTERNAL (scheduling).** `create_reminder` writes
a SQLite row only, never claims an alarm was scheduled. Real scheduling/delivery is
Backend/Flutter-owned, undecided, not built here — correctly not implemented.

## 11. Freshness, provenance, and safe personalization
**VOICEBOT PASS.** Provenance tags (`source`, `created_by`) distinguish caregiver-synced,
patient-provided, and assistant-generated content; `is_deceased` passed through as context,
never inferred; prompt-injection resistance verified at both the sanitization and
deterministic-gate layers (`tests/integration/test_memory_prompt_injection.py`, 6 tests).

## 12. Audio interaction and interruption
**VOICEBOT PASS (its half) / FLUTTER EXTERNAL (the rest).** Cancellable jobs, stable
job/audio identity, ownership enforced, expiry enforced at read time. Alarm priority,
playback interruption, and re-pairing/discard behavior are Flutter's own responsibility by
design — documented in `docs/FLUTTER_VOICEBOT_INTEGRATION.md` §9–15, not implemented here.

## 13. Offline and degraded behavior
**VOICEBOT PASS (documentation) / JOINT EXTERNAL (device-offline behavior).** Four cases
distinguished (VoiceBot-reachable-LLM-down, VoiceBot-unreachable, tablet-offline,
language-unavailable) in `docs/VOICEBOT_INTEGRATION_GUIDE.md` §17. Tablet-offline behavior
itself is Flutter's own local reminders/games/media, outside this repository.

## 14. Privacy, retention, and lifecycle
**VOICEBOT PASS.** No API keys, auth headers, raw phone numbers, raw audio, full
transcripts, full memory payloads, or signed URLs in logs — verified
(`tests/unit/test_privacy_data_minimization.py`). 500 responses are always a fixed safe
string, never `str(exc)`.

## 15. Deployment and operations
**VOICEBOT PASS (plan) / real deployment BLOCKED_EXTERNAL.** Exact-pinned dependencies
verified in a clean venv; migrations automatic; health/readiness distinguished; backup
tooling exists and is tested (`tests/unit/test_backup_db.py`). `docs/AWS_DEPLOYMENT.md` +
`deployment/aws/` are the ready-to-run plan — actual provisioning is blocked on AWS
credentials not being configured in this environment (confirmed: no `aws` CLI, no
`~/.aws/`, no `AWS_*` env vars).

## 16. API documentation and compatibility
**VOICEBOT PASS.** `docs/integration/openapi.json` generated directly from the live app
(cannot drift from real routes); Swagger's previously-missing `x-api-key` security scheme
fixed this project (`tests/unit/test_openapi_security.py`); error catalogue, language
matrix, memory/action schemas all machine-readable and regenerated this session.

## 17. What the Backend/web team provides
**Status changed since the last audit.** The client repository (`Abhayk777/SMRITI`) has
moved from "no VoiceBot integration surface at all" to having an explicit
`VOICEBOT-INTEGRATION-PLAN.md` with a phased work-package breakdown (VB-00 through VB-09).
Its own status line: "proposed implementation work; nothing in this checklist is certified
complete." **BACKEND EXTERNAL** — the gateway, Supabase auth wiring, and durable content
sync described in that plan remain unbuilt as of this session's inspection.

## 18. Features intentionally outside this integration
**VOICEBOT PASS.** Cognitive diagnosis, prescription OCR, face recognition, clinical
reporting, game scoring, and offline conversational AI are all confirmed absent from this
repository — none silently implied by anything shipped.

## 19. Required acceptance evidence
**VOICEBOT PASS (automated suite) / JOINT EXTERNAL (physical device).** Cross-patient
isolation, session-restart ownership, provisioning/revocation, sync edge cases, structured
schedules, deceased handling, prompt injection (text/voice/memory), invalid/oversized audio,
queue/restart/timeout/cancellation/expiry, duplicate-action prevention, confirmation expiry,
provider-failure honesty, and secret redaction are all covered by real tests in this
repository's own `pytest -q` (655 passing). The physical-device joint acceptance run itself
(`docs/RELEASE_ACCEPTANCE.md`'s EXTERNAL table) has not happened and cannot happen from this
repository alone.
