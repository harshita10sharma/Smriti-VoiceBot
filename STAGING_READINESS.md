# Staging / pilot readiness checklist

Status legend: **READY** (demonstrated, with evidence cited) · **PARTIAL** (works, with a
known real limitation) · **BLOCKED** (does not exist) · **REQUIRES BACKEND** ·
**REQUIRES FLUTTER** · **REQUIRES JOINT VALIDATION**.

Nothing below is marked READY without a concrete test, file, or command that demonstrates
it — see the evidence column.

---

## AUTH

| Item | Status | Evidence |
|---|---|---|
| Server-side credentials (never client-side) | **READY** | `x-api-key` model, `SECURITY.md` §Authentication; provider keys never returned by any endpoint |
| Patient mapping (`user_id`) | **READY** | `INTEGRATION_CONTRACT.md` §1; `user_id`/`external_id` distinction |
| Revocation | **READY** | `POST /v1/memory/sync`'s `active` field; `tests/integration/test_patient_lifecycle.py::test_memory_sync_can_disable_a_patient` / `test_memory_sync_can_re_enable_a_disabled_patient` |
| Secret rotation | **PARTIAL** | Rotating `SMRITI_API_KEY`/provider keys requires an env var change + restart (no in-place reload); no automation exists for this |
| Fail-closed unauthenticated mode | **READY** | `SMRITI_ALLOW_UNAUTHENTICATED` requires `SMRITI_ENV=development` or the app refuses to start; `tests/unit/test_unauthenticated_mode_guard.py` |

## DATABASE

| Item | Status | Evidence |
|---|---|---|
| Migrations | **READY** | Append-only, `PRAGMA user_version`-gated; verified against the real running deployment across 6 schema versions with zero data loss |
| Backup | **READY** | `tools/backup_db.py backup` (SQLite online-backup API, safe against a live DB); `tests/unit/test_backup_db.py` |
| Restore | **READY** | `tools/backup_db.py restore` (preserves the pre-restore file as `.pre-restore`); same test file |
| Schema verification | **READY** | `tools/backup_db.py verify` (integrity check + schema version report); run against the real `runtime/smriti.db` during this batch with no corruption found |

## VOICE

| Item | Status | Evidence |
|---|---|---|
| ASR | **READY (Sarvam path)** | Live Sarvam ASR calls confirmed against the real API this session (real latency recorded, correctly reported `NO_SPEECH_DETECTED` for a silent/tone input); local ONNX models real-validated for brx/mni/npi/hin in a prior session with network access, asm downgraded (upstream model repo empty) |
| LLM | **READY (Groq path)** | Live Groq calls succeeded repeatedly this session (e.g. 616ms–8955ms latency samples recorded in `SECURITY.md`); other providers remain code-complete but unverified live |
| TTS | **READY (Sarvam path)** | A prior deployment shipped with the default model pinned to a since-deprecated Sarvam Bulbul version (`bulbul:v2`) and a speaker name that version no longer accepts, so every real synthesis call failed with `TTS_UNAVAILABLE`. Root-caused against the live API (confirmed via direct HTTP call, not assumption) and fixed by moving the default model to `bulbul:v3` and the default speaker to `anand` (verified compatible with `bulbul:v3` across every configured Sarvam TTS language). A full welcome→job→completed→audio-fetch round trip against the real API now returns genuine playable audio; Indic Parler-TTS real-validated in a prior session with a granted `HF_TOKEN` for asm/brx/mni/npi |
| Queue (bounded) | **READY** | `SMRITI_VOICE_JOB_QUEUE_MAX`; `tests/integration/test_voice_job_reliability.py` |
| Cancellation | **READY** | `POST /v1/voice/jobs/{id}/cancel`, race-safe against the worker; `tests/integration/test_voice_job_cancellation_and_deadline.py` |
| Timeout | **READY** | `SMRITI_VOICE_JOB_PROCESSING_DEADLINE_S`, enforced at read time; same test file |

## API

| Item | Status | Evidence |
|---|---|---|
| Health | **READY** | Liveness-only by design, never gated on optional providers; `tests/unit/test_health_liveness_semantics.py` |
| Authentication | **READY** | See AUTH above |
| Ownership | **READY** | 404-not-403 for cross-patient job/audio; 403 for cross-patient session/conversation; `tests/integration/test_e2e_contract_harness.py::test_cross_patient_isolation_across_the_full_chain` |
| Errors | **READY** | Documented exact envelope shape (FastAPI default, no breaking change introduced); `INTEGRATION_CONTRACT.md` §7 |
| Idempotency | **READY** | `Idempotency-Key` on conversation/voice; per-`(user_id, key)` scoping; `tests/integration/test_idempotency.py`, `test_action_state_machine.py::test_retried_confirmation_with_same_idempotency_key_executes_once` |

## PRIVACY

| Item | Status | Evidence |
|---|---|---|
| Logging | **READY** | `logging.py`'s key-name/value-pattern redaction; audio/phone/transcript keys excluded from log payloads by construction |
| Credentials | **READY** | `/v1/health` reports booleans only; verified no tracked file contains a live-looking secret pattern |
| Audio | **READY** | Never travels in JSON/logs; opaque `audio_id`, fetch-once semantics |
| Memory | **READY** | Every repository query is `user_id`-scoped at the SQL level, not caller discipline |
| Transcripts | **PARTIAL** | Not logged by default; the smoke test's remote mode redacts `response_text` unless `--show-text` is passed explicitly — but nothing prevents an operator from turning up `SMRITI_LOG_LEVEL`/adding ad hoc logging that captures one; this is a code-review discipline, not an enforced technical control beyond what's redacted today |

## OPERATIONS

| Item | Status | Evidence |
|---|---|---|
| One-worker limitation | **PARTIAL** | Enforced by the Dockerfile's hardcoded `--workers 1`, not by runtime detection (a process cannot reliably detect sibling workers); best-effort `WEB_CONCURRENCY`/`UVICORN_WORKERS` guard exists (`tools/validate_config.py`, startup log) |
| Restart behavior | **READY** | Sessions/confirmations (SQLite-backed) and voice jobs (marked `INTERRUPTED_BY_RESTART`) both survive/handle a restart correctly; `tests/integration/test_persistent_sessions.py` |
| Monitoring | **BLOCKED** | No metrics/alerting exists in this repository; `/v1/health` is the only machine-checkable signal today |
| Backup | **READY** | See DATABASE above |
| Rollback | **PARTIAL** | Migrations are forward-only and additive; rolling back application code to an older commit against a newer-schema database is untested and not recommended without restoring a matching backup first |

## INTEGRATION

| Item | Status | Evidence |
|---|---|---|
| Backend contract | **READY** | `INTEGRATION_CONTRACT.md`, verified against actual source, not assumed |
| Flutter contract | **READY** | Same document, §9 |
| E2E tests | **READY** | `tests/integration/test_e2e_contract_harness.py` (9 deterministic scenarios covering the full chain, cross-patient isolation, revocation, idempotency, cancellation races, expiry, provider-failure truthfulness) |
| Staging smoke test | **READY** | `tools/smoke_test.py --base-url <url> --voice` — verified this batch against a real locally-running server (health, languages, welcome, conversation, voice turn all passed over real HTTP) |

---

## What this checklist does NOT claim

- **Real, native-speaker language quality validation** for any language — `config/language_validation.json`'s validated set is empty; every language reports at most `BENCHMARK_ONLY`/`NOT_YET_TESTED` in `/v1/languages`, by design, and this document does not claim otherwise.
- **Production-grade availability.** This remains a single-host, single-worker, SQLite-backed pilot/staging architecture — appropriate for the current pilot scale, not evaluated for concurrent multi-patient production load.
- **Physical-device acceptance.** The E2E harness is a deterministic, mocked-provider proof of the contract's logic, not a live-network or real-hardware test.
- **Controlled calling or reminder scheduling.** No real executor exists for either — see `INTEGRATION_CONTRACT.md` §11.
