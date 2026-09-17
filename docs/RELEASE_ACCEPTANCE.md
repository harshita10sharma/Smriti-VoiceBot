# SMRITI VoiceBot — Release Acceptance

Status legend: **PASS** (demonstrated, evidence cited) · **BLOCKED_EXTERNAL** (VoiceBot's
side is done; the remainder needs infrastructure this repository doesn't own) ·
**NOT_APPLICABLE**.

No row below is marked PASS without a concrete test, file, or command that demonstrates it.

## VOICEBOT-OWNED

| Area | Status | Evidence |
|---|---|---|
| **Live AWS deployment** | **PASS** | `https://15-206-144-216.nip.io` — real EC2 (`m7i-flex.large`, `ap-south-1`), real Let's Encrypt HTTPS via Caddy, persistent EBS, verified to survive both a container restart and a full instance reboot. Real Groq/Sarvam/Indic Parler, async voice jobs, cancellation, audio retrieval, memory sync, and patient isolation all verified live against this deployment. See `docs/AWS_DEPLOYMENT.md` §15. |
| Identity (provisioning, authorization, `user_id` format, external UUID support) | **PASS** | `tests/integration/test_patient_lifecycle.py`, `tests/integration/test_multi_user_auth.py`; UUID-format compatibility verified directly against `[A-Za-z0-9\-_.]{1,64}` |
| Dynamic multi-patient authorization (grows automatically per-patient, never a wildcard, rotation-safe via a stable `id`) | **PASS** | `tests/integration/test_dynamic_backend_key_provisioning.py`; live: verified simultaneous-patient isolation, cross-patient session-swap rejection, and independent disable/re-enable against the real AWS deployment (2026-09-16); grant survival across a real secret rotation confirmed live |
| Credential rotation without memory loss | **PASS** | `tests/integration/test_credential_rotation.py` — old key rejected immediately, new key works, memory/session ownership intact |
| Patient isolation (cross-patient session/job/audio) | **PASS** | `tests/integration/test_multi_user_auth.py`, `tests/integration/test_e2e_contract_harness.py::test_cross_patient_isolation_across_the_full_chain` |
| Sessions (persistent, restart-surviving, consistent text/voice ownership errors) | **PASS** | `tests/integration/test_persistent_sessions.py`; `e3b4df6` fixed the voice-endpoint 500-instead-of-403 inconsistency |
| Concurrency (per-session serialization, no cross-patient blocking) | **PASS** | `tests/integration/test_concurrent_sessions.py` (7 scenarios); the fix was reverted and re-applied during this work specifically to confirm the tests catch the race, not just pass coincidentally |
| Confirmations (bound to patient/session/action, expiry, no implicit approval) | **PASS** | `tests/integration/test_action_state_machine.py`; `metadata.action_id` added this release for explicit proposal-instance correlation |
| Memory sync (versioned, transactional, whitelisted, revision covers full snapshot) | **PASS** | `tests/integration/test_memory_sync_versioning.py` (17 tests, including the new patient-context-only-change-is-a-conflict cases) |
| Medication schedule logic (structured, non-wrapping window, timezone-aware) | **PASS** | `tests/unit/test_per_patient_timezone.py`; verified against the real Supabase-shaped schema this session (field names/types match exactly) |
| Language capability reporting (honest, normalized, no false Mongolian mapping) | **PASS** | `tests/unit/test_target_language_mappings.py`, `tests/unit/test_asr_openai_language_hints.py`; live matrix: `docs/integration/language_matrix.json` |
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
| Deployment documentation (AWS-aware, pilot vs. production distinguished) | **PASS** | `docs/AWS_DEPLOYMENT.md` (current target); Tailscale Funnel and the earlier Azure evaluation both explicitly marked historical/pilot-only |
| OpenAPI consistency | **PASS** | `docs/integration/openapi.json`, generated directly from the live app (`app.openapi()`), not hand-maintained — cannot drift from the real routes |
| Documentation completeness (Backend/Flutter can integrate without reading source) | **PASS** | `docs/VOICEBOT_INTEGRATION_GUIDE.md`, `docs/BACKEND_VOICEBOT_INTEGRATION.md`, `docs/FLUTTER_VOICEBOT_INTEGRATION.md` |
| Test fixtures (isolation, empty datasets, deceased, multi-med, revisions, injection, provider failure) | **PASS** | `integration/fixtures/scenario_fixtures.json` (captured live this session) plus the pre-existing fixture set |
| Deterministic test mode (labeled, cannot masquerade as real) | **PASS** | `TESTING.md`; `SMRITI_LLM_PROVIDER=mock`/`SMRITI_TTS_PROVIDER=mock` are explicit opt-ins, never auto-selected — confirmed by reading `llm/router.py`/`tts/router.py`'s candidate-selection logic, which only chooses `mock` when explicitly named |
| Indic Parler-TTS generation timeout enforcement | **PASS** | `tests/unit/test_tts_indic_parler.py::test_synthesize_raises_provider_timeout_instead_of_hanging_forever` — a real, previously-unenforced defect (the `timeout` constructor argument was accepted but never actually bounded `model.generate()`, so a stuck generation could block the single TTS worker indefinitely) found via a Backend-team integration review, reproduced, and fixed |
| Response-language honesty for deterministic (non-LLM) replies | **PASS** | `tests/integration/test_response_language_honesty.py` (8 tests) — a real, confirmed defect (welcome greeting for `language='mni'` returned English text while still reporting `language: "mni"`) found via the same review, reproduced, and fixed across welcome/refusal/confirmation/action-reply/ASR-error paths |
| Health capability reporting completeness | **PASS** | `tests/unit/test_health_liveness_semantics.py` — `capabilities.general_conversation` previously omitted `'groq'` (this repository's own primary/documented LLM provider) from its checked provider list entirely, silently under-reporting the capability on any deployment without also configuring a different listed provider; fixed to derive from the actual configured-provider set instead of a hand-maintained name list |
| Memory-sync provisioning for a brand-new authorized patient | **PASS**, re-verified | Direct real-HTTP test this session: a never-before-seen `user_id` syncing non-empty family/medicine data succeeds (`200`, `status: "applied"`) — a Backend-team review flagged this as broken in an earlier snapshot; confirmed already fixed in current source, not re-fixed |
| Structured medicine-schedule deterministic filtering | **PASS**, re-verified | `smriti_voice/memory/service.py::medicines()` — real weekday/time-window filtering against `days_of_week`/`chosen_time_min` confirmed present in current source; a Backend-team review's "schedule written into instructions, structured time empty" finding does not reproduce against current code |

## EXTERNAL (genuinely outside this repository's ownership)

| Item | Why it's external |
|---|---|
| Backend gateway implementation | **Planning exists, implementation does not.** The client repository (`Abhayk777/SMRITI`) now has `VOICEBOT-INTEGRATION-PLAN.md` (its own status line: "proposed implementation work; nothing in this checklist is certified complete") — no edge function, schema, or credential handling for VoiceBot has actually been built there yet. `docs/BACKEND_VOICEBOT_INTEGRATION.md` remains the exact handoff for when it is. |
| Flutter implementation | Not in any repository this session has access to. `docs/FLUTTER_VOICEBOT_INTEGRATION.md` is the handoff. |
| Supabase integration (the actual gateway calling both Supabase and VoiceBot) | Same as Backend gateway above — planned, not built, external. |
| Real device / physical-device acceptance | Requires actual hardware and the above two components; `STAGING_READINESS.md` lists exactly what VoiceBot-side testing already supports this (cancellation, ownership, retry, expiry, idempotency — all real-HTTP verified). |
| Native-speaker language quality validation | Requires human native speakers; `LANGUAGE_SUPPORT.md` and `/v1/languages` honestly report `validated: false` for every language rather than pretending otherwise. |
| Physical alarm interruption / physical network loss / re-pairing | Requires the physical device and Flutter's own audio-arbitration code; VoiceBot exposes the identifiers (`job_id`, `session_id`, `audio_id`) needed to make this possible, documented in `docs/FLUTTER_VOICEBOT_INTEGRATION.md` §9–13. |
| Controlled calling / conversational reminder execution | Explicitly gated off pending a real executor — see `INTEGRATION_CONTRACT.md` §9–10 and `docs/VOICEBOT_INTEGRATION_GUIDE.md` §15–16. Building the executor is Backend/Flutter-owned by design. |

## What "PASS" means here

Every PASS row above is backed by an automated test that runs in this repository's own
`pytest -q` (625 tests, all passing as of this release), by a real-provider validation run
against live Sarvam/Groq/Hugging Face credentials, or by a real HTTP call against the live
deployment at `https://15-206-144-216.nip.io`. None of it substitutes for the EXTERNAL items
above — those require infrastructure and people this repository cannot provide.
