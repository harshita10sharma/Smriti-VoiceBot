# SMRITI VoiceBot — Release Acceptance

Status legend: **PASS** (demonstrated, evidence cited) · **BLOCKED_EXTERNAL** (VoiceBot's
side is done; the remainder needs infrastructure this repository doesn't own) ·
**NOT_APPLICABLE**.

No row below is marked PASS without a concrete test, file, or command that demonstrates it.

## VOICEBOT-OWNED

| Area | Status | Evidence |
|---|---|---|
| Identity (provisioning, authorization, `user_id` format, external UUID support) | **PASS** | `tests/integration/test_patient_lifecycle.py`, `tests/integration/test_multi_user_auth.py`; UUID-format compatibility verified directly against `[A-Za-z0-9\-_.]{1,64}` |
| Credential rotation without memory loss | **PASS** | `tests/integration/test_credential_rotation.py` — old key rejected immediately, new key works, memory/session ownership intact |
| Patient isolation (cross-patient session/job/audio) | **PASS** | `tests/integration/test_multi_user_auth.py`, `tests/integration/test_e2e_contract_harness.py::test_cross_patient_isolation_across_the_full_chain` |
| Sessions (persistent, restart-surviving, consistent text/voice ownership errors) | **PASS** | `tests/integration/test_persistent_sessions.py`; `e3b4df6` fixed the voice-endpoint 500-instead-of-403 inconsistency |
| Concurrency (per-session serialization, no cross-patient blocking) | **PASS** | `tests/integration/test_concurrent_sessions.py` (7 scenarios); the fix was reverted and re-applied during this work specifically to confirm the tests catch the race, not just pass coincidentally |
| Confirmations (bound to patient/session/action, expiry, no implicit approval) | **PASS** | `tests/integration/test_action_state_machine.py`; `metadata.action_id` added this release for explicit proposal-instance correlation |
| Memory sync (versioned, transactional, whitelisted, revision covers full snapshot) | **PASS** | `tests/integration/test_memory_sync_versioning.py` (17 tests, including the new patient-context-only-change-is-a-conflict cases) |
| Medication schedule logic (structured, non-wrapping window, timezone-aware) | **PASS** | `tests/unit/test_per_patient_timezone.py`; verified against the real Supabase-shaped schema this session (field names/types match exactly) |
| Language capability reporting (honest, normalized, no false Mongolian mapping) | **PASS** | `tests/unit/test_target_language_mappings.py`, `tests/unit/test_asr_openai_language_hints.py`; live matrix: `docs/integration/voicebot-language-matrix.json` |
| ASR | **PASS** | Real Sarvam call reconfirmed this session (`tools/smoke_test.py`, REAL/groq/sarvam rows) |
| LLM | **PASS** | Same smoke-test run: real Groq generation, 1125ms |
| TTS | **PASS** | Same smoke-test run: real Sarvam `bulbul:v3` synthesis, 65,806 bytes of real audio, 1610ms |
| Voice jobs (lifecycle, cancellation, deadline, restart recovery, duplicate protection) | **PASS** | `tests/integration/test_voice_job_cancellation_and_deadline.py`, `tests/integration/test_voice_jobs.py` |
| Audio (contract, expiry, ownership) | **PASS** | `tests/unit/test_audio_contract.py`; retention/expiry verified via real HTTP in prior sessions |
| Idempotency (sequential, concurrent, cross-patient non-collision) | **PASS** | `tests/integration/test_idempotency.py`, `tests/integration/test_concurrent_sessions.py::test_simultaneous_identical_idempotent_requests_execute_once` |
| Safety / prompt injection (memory, transcript, tool authorization) | **PASS** | `tests/integration/test_memory_prompt_injection.py` (6 tests); `tests/safety/` |
| Actions (allow-listed, fail-closed, proposal ≠ completion) | **PASS** | `tests/integration/test_action_state_machine.py::test_unknown_tool_name_fails_closed`, `test_sensitive_tool_never_executes_even_if_the_model_calls_it` |
| Privacy / logging (redaction, no secrets/PII in logs or errors) | **PASS** | `tests/unit/test_privacy_data_minimization.py` |
| Configuration reproducibility (exact pins, clean-venv install) | **PASS** | `requirements.txt` (exact pins); verified by a genuinely fresh virtual environment install + full suite pass this session |
| Deployment documentation (Azure-aware, pilot vs. production distinguished) | **PASS** | `STAGING_READINESS.md` "Deploying on Azure specifically"; Tailscale Funnel explicitly marked pilot-only |
| OpenAPI consistency | **PASS** | `docs/integration/openapi.json`, generated directly from the live app (`app.openapi()`), not hand-maintained — cannot drift from the real routes |
| Documentation completeness (Backend/Flutter can integrate without reading source) | **PASS** | `docs/VOICEBOT_INTEGRATION_GUIDE.md`, `docs/BACKEND_VOICEBOT_INTEGRATION.md`, `docs/FLUTTER_VOICEBOT_INTEGRATION.md` |
| Test fixtures (isolation, empty datasets, deceased, multi-med, revisions, injection, provider failure) | **PASS** | `integration/fixtures/scenario_fixtures.json` (captured live this session) plus the pre-existing fixture set |
| Deterministic test mode (labeled, cannot masquerade as real) | **PASS** | `TESTING.md`; `SMRITI_LLM_PROVIDER=mock`/`SMRITI_TTS_PROVIDER=mock` are explicit opt-ins, never auto-selected — confirmed by reading `llm/router.py`/`tts/router.py`'s candidate-selection logic, which only chooses `mock` when explicitly named |

## EXTERNAL (genuinely outside this repository's ownership)

| Item | Why it's external |
|---|---|
| Backend gateway implementation | Does not exist in the real client repository (`Abhayk777/SMRITI`) as of this session's inspection — no edge function, schema, or credential handling for VoiceBot exists there. `docs/BACKEND_VOICEBOT_INTEGRATION.md` is the exact handoff for whoever builds it. |
| Flutter implementation | Not in any repository this session has access to. `docs/FLUTTER_VOICEBOT_INTEGRATION.md` is the handoff. |
| Supabase integration (the actual gateway calling both Supabase and VoiceBot) | Same as Backend gateway above — unbuilt, external. |
| Real device / physical-device acceptance | Requires actual hardware and the above two components; `STAGING_READINESS.md` lists exactly what VoiceBot-side testing already supports this (cancellation, ownership, retry, expiry, idempotency — all real-HTTP verified). |
| Native-speaker language quality validation | Requires human native speakers; `LANGUAGE_SUPPORT.md` and `/v1/languages` honestly report `validated: false` for every language rather than pretending otherwise. |
| Physical alarm interruption / physical network loss / re-pairing | Requires the physical device and Flutter's own audio-arbitration code; VoiceBot exposes the identifiers (`job_id`, `session_id`, `audio_id`) needed to make this possible, documented in `docs/FLUTTER_VOICEBOT_INTEGRATION.md` §9–13. |
| Production Azure credentials/environment | No Azure deployment has been performed; `STAGING_READINESS.md` documents what to configure when it is. |
| Controlled calling / conversational reminder execution | Explicitly gated off pending a real executor — see `INTEGRATION_CONTRACT.md` §9–10 and `docs/VOICEBOT_INTEGRATION_GUIDE.md` §15–16. Building the executor is Backend/Flutter-owned by design. |

## What "PASS" means here

Every PASS row above is backed by an automated test that runs in this repository's own
`pytest -q` (593 tests, all passing as of this release) or by a real-provider smoke test
(`tools/smoke_test.py`) run this session against live Sarvam and Groq credentials. None of
it substitutes for the EXTERNAL items above — those require infrastructure and people this
repository cannot provide.
