# Contract fixtures

Deterministic example requests/responses for the Backend and Flutter teams to build
against. Read alongside `INTEGRATION_CONTRACT.md`, which is the authoritative reference for
field meanings, status codes, and ownership rules — these files are examples, not a second
source of truth.

## Provenance

Most files here were **captured from real HTTP responses** of this codebase's actual FastAPI
app (`TestClient`, mock LLM/TTS providers so the exact text is reproducible — a real
deployment's `llm_provider`/`tts_provider` will read `"groq"`/`"sarvam"`/`"indic_parler"`
etc. instead of `"mock"`, and `response_text` will be real model output, not the fixed mock
string shown here):

- `welcome_request.json` / `welcome_response.json`
- `conversation_request.json` / `conversation_response.json`
- `voice_response.json`
- `memory_sync_request.json` / `memory_sync_response.json`
- `job_completed.json`
- `scenario_fixtures.json` — two-patient isolation, empty datasets, a deceased relative,
  multiple medications, and stale/conflicting memory-sync revisions, all captured live
  against a real running app instance the same way as the files above. Its
  `prompt_injection_memory` and `provider_failure` entries are hand-authored (the same
  convention as the job-state files below) since they document a guarantee/incident
  rather than a single capturable response — each names the automated test or changelog
  entry that is the actual evidence.
- `error_examples.json`

A few job-state fixtures represent states that are real but timing-dependent to capture
reliably in a one-shot script (a job is `queued`/`processing` only briefly under normal
conditions) — these are **hand-authored to match the verified `VoiceJobStatusResponse`
schema** (see `schemas.py` and `tests/integration/test_voice_job_cancellation_and_deadline
.py`), not literally captured:

- `job_queued.json`, `job_processing.json`, `job_failed.json`, `job_cancelled.json`

`voice_request.md` documents the multipart request shape (not representable as plain JSON).

## Field conventions

- `null` means the field is genuinely absent in that state (e.g. `audio_id` is always `null`
  until a job reaches `completed`) — not "optional, may or may not appear." Every field
  listed always appears in the response; only its value varies.
- Fields ending in `_id` (`request_id`, `session_id`, `job_id`, `audio_id`) are opaque
  strings — do not parse or assume a format beyond "safe to echo back verbatim."
- `status` values across the job fixtures (`queued`/`processing`/`completed`/`failed`/
  `cancelled`) are the complete, exhaustive set — there is no other terminal or
  non-terminal state.
- `error_code` is `null` except on a `failed`/`cancelled` job — when set, it is one of the
  stable codes listed in `INTEGRATION_CONTRACT.md` §7, safe to branch on.
