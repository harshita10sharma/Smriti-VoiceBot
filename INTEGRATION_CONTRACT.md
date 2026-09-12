# SMRITI VoiceBot — Integration Contract

**Audience:** the Backend and Flutter teams building against this VoiceBot service.
**Status:** this describes the ACTUAL, verified behavior of the codebase as of the commit
this file was published with — not a plan, not a proposal, not an aspiration. Every claim
below was checked against source and, where marked, against a real running deployment or a
deterministic test harness (`tests/integration/test_e2e_contract_harness.py`).

This supersedes nothing — `HANDOFF.md`, `SECURITY.md` and `LANGUAGE_SUPPORT.md` remain the
detailed references for their topics and are consistent with this document. Use this file as
the single starting point; follow the cross-references for depth.

---

## 0. Ownership map

| Concern | Owner |
|---|---|
| ASR, conversation routing, personal-memory storage, deterministic safety, action proposal/validation/confirmation, TTS, voice-job lifecycle, audio storage | **VoiceBot** (this repository) |
| Authenticating the Flutter device/app, authorizing a patient, provisioning/revoking a patient identity, holding the VoiceBot server-side credential, synchronizing authoritative caregiver data (family/medicine/routine) into VoiceBot, retry/backoff, privacy/consent gating on top of VoiceBot's own | **Backend** |
| Recording audio, calling the Backend (never VoiceBot directly with an end-user credential), displaying `response_text` and playing audio, polling/cancelling voice jobs, executing an action only when `action_accepted`/an authenticated executor confirms it, alarm/media priority over AI audio | **Flutter** |
| Full physical-device acceptance, multi-patient pilot rollout, cross-system latency under real network conditions | **Joint**, not owned by any one team alone |

### DO NOT IMPLEMENT IN VOICEBOT

The following are explicitly out of scope for this repository and were not added in any
integration batch to date. Do not infer they exist from adjacent-sounding code:

- Twilio or any real telephony execution. `call_family_member` (the tool) only ever returns
  a data dict (`{'called': True, 'name': ..., 'relation': ...}`) — nothing dials a phone.
- Conversational-reminder scheduling/alarms. `create_reminder` only writes a SQLite row; no
  device alarm is scheduled by VoiceBot.
- Any Flutter UI, caregiver dashboard, or Supabase schema.
- Cognitive diagnosis, prescription OCR, face recognition, clinical reporting, game
  scoring, or offline (on-device) conversational AI.
- A second memory system, a second action/confirmation system, a second voice-job system,
  or a second idempotency system. There is exactly one of each; reuse it.

---

## 1. Authentication & patient identity

See `SECURITY.md` §Authentication for full detail; summary for integration purposes:

- Header: `x-api-key: <key>`.
- **Single-user mode** (`SMRITI_API_KEY` + `SMRITI_AUTH_USER_ID`): one key, one fixed
  `user_id`. This is the current live pilot configuration (`elder-1`).
- **Multi-user mode** (`SMRITI_API_KEYS`, a JSON map): each key maps to one `user_id`
  (string) or an explicit list of `user_id`s (a genuine multi-patient backend key). A caller
  can never claim a `user_id` outside what its key authorizes — every route checks this
  server-side, never trusting the request body alone.
- **Identity VoiceBot expects**: `user_id` is VoiceBot's own primary key for a patient —
  any string matching `[A-Za-z0-9\-_.]{1,64}`. **Send your Backend's own stable patient
  identifier here** (e.g. a Supabase UUID) if you want a 1:1 mapping with no translation
  layer; VoiceBot does not require or assume any particular format.
- **`external_id`** (on `users`, and per-record on `family_members`/`medicines`/
  `daily_routines`) is a *second*, optional, explicitly-for-the-backend field — set it via
  `POST /v1/memory/sync` if you want VoiceBot to also remember your own record ids
  independently of whatever you chose as `user_id`. It is unique (globally for the patient
  `external_id`, per-patient for record-level ones) — reusing one for a different owner
  returns a clean `409`, not a crash (verified:
  `test_external_id_collision_across_patients_is_a_clean_conflict`).
- **Provisioning is separate from authorization.** A `user_id` your key authorizes but that
  has never been synced/talked-to yet is served normally (not "denied") — VoiceBot
  auto-provisions a bare `users` row on first contact (memory sync or first conversation
  turn). Authorization is checked first and independently, from the key config, never from
  whether a `users` row exists.
- **Disabled patient**: `users.active = 0` — set via `POST /v1/memory/sync`'s `active` field
  (see §8's revocation note; no separate admin endpoint) — makes every conversational/voice
  endpoint return `403` for that patient, even for an otherwise-valid, authorized key.
  `POST /v1/memory/sync` itself remains reachable regardless of `active`, since it's the only
  path that can restore it. Verified end-to-end:
  `test_disabled_patient_is_rejected_at_every_conversational_entry_point`,
  `test_memory_sync_can_disable_a_patient`, `test_memory_sync_can_re_enable_a_disabled_patient`.
- **Cross-patient access**: fails closed. `403` for a session/action outside your
  authorized set; `404` (not `403`) for a job/audio id that either doesn't exist or belongs
  to a patient you're not authorized for — a job id can never be used to probe for another
  patient's data. Verified: `test_cross_patient_isolation_across_the_full_chain`.
- **VoiceBot credentials never reach Flutter.** Your Backend holds `x-api-key`; provider
  keys (`GROQ_API_KEY`, `SARVAM_API_KEY`, `HF_TOKEN`, etc.) never leave the server and are
  never present in any response body.

---

## 2. Endpoint reference

All request/response fields below are exactly what the Pydantic models in `schemas.py`
declare — nothing here is aspirational.

| # | Method & path | Auth | Patient field | Intended caller |
|---|---|---|---|---|
| 1 | `GET /v1/health` | none | — | Uptime monitor, Backend |
| 2 | `GET /v1/languages` | none | — | Backend (capability planning) |
| 3 | `GET /v1/languages/{code}` | none | — | Backend |
| 4 | `POST /v1/conversation` | key | `user_id` (body) | Backend (proxying Flutter text turns) |
| 5 | `POST /v1/conversation/voice` | key | `user_id` (form) | Backend (proxying Flutter voice turns) |
| 6 | `POST /v1/conversation/welcome` | key | `user_id` (body) | Backend (proxying app-open) |
| 7 | `GET /v1/voice/jobs/{job_id}` | key | resolved from job | Backend/Flutter (poll) |
| 8 | `POST /v1/voice/jobs/{job_id}/cancel` | key | resolved from job | Backend/Flutter |
| 9 | `GET /v1/audio/{audio_id}` | key | resolved from job | Backend/Flutter |
| 10 | `POST /v1/memory/sync` | key | `user_id` (body) | **Backend only** |
| 11 | `POST /v1/command` | key | none (legacy) | Backend (legacy v4.1 clients only) |
| 12 | `GET /v1/tools` | key | — | Backend (introspection/debugging) |

### 4. `POST /v1/conversation`
```jsonc
// request
{"user_id": "elder-1", "session_id": null, "message": "What is my daughter's name?",
 "language": "eng"}
// response (ConversationResponse)
{"request_id": "...", "session_id": "...", "response_text": "...", "language": "eng",
 "language_confidence": 0.0, "kind": "MEMORY", "action": "NO_ACTION",
 "action_accepted": false, "tool_calls": [...], "tool_results": [...], "safety": null,
 "requires_confirmation": false,
 "metadata": {"request_id": "...", "session_id": "...", "kind": "MEMORY",
              "execution_mode": "ONLINE_PRIMARY", "offline": false, "fallback_used": false,
              "llm_provider": "groq", "llm_latency_ms": 616, "tool_latency_ms": 4,
              "total_latency_ms": 640, "error_code": null, "topic": "personal"}}
```
`kind` ∈ `COMMAND | CONVERSATION | MEMORY | CONFIRMATION | REFUSAL | FALLBACK | ERROR |
WELCOME`. Idempotency: optional `Idempotency-Key` header — see §5.

### 5. `POST /v1/conversation/voice` (multipart)
Fields: `audio_wav` (file, required), `user_id`, `session_id` (optional), `language`
(optional), `speak` (default `true`). Response = `ConversationResponse` fields +
`transcript`, `job_id`, `job_status`, `audio_id` (always `null` here — TTS is async),
`audio_url` (`null`), `audio_available` (`false`), `audio_unavailable_reason`,
`tts_provider` (`null`). See §3 for the audio contract and §4 for the job lifecycle.

### 6. `POST /v1/conversation/welcome`
```jsonc
// request
{"user_id": "elder-1", "session_id": null, "language": "eng", "speak": false}
// response (WelcomeResponse)
{"request_id": "...", "session_id": "...", "response_text": "Hello, Elder 1. I am here...",
 "language": "eng", "kind": "WELCOME", "session_restored": false,
 "job_id": null, "job_status": "NOT_REQUESTED", "audio_id": null, "audio_url": null,
 "audio_available": false, "audio_unavailable_reason": "TTS_NOT_REQUESTED",
 "tts_provider": null}
```
See §10 for the full welcome contract.

### 7/8/9. Voice job status / cancel / audio
```jsonc
// GET /v1/voice/jobs/{job_id}
{"job_id": "...", "status": "completed", "language": "eng", "audio_id": "...",
 "audio_url": "/v1/audio/...", "tts_provider": "mock", "error_code": null,
 "audio_expired": false}
// POST /v1/voice/jobs/{job_id}/cancel
{"job_id": "...", "status": "cancelled", "cancelled": true}
// GET /v1/audio/{audio_id} -> raw audio/wav bytes, fetch once, promptly
```
See §4 for the complete lifecycle.

### 10. `POST /v1/memory/sync`
See §5 (idempotency is naturally handled by full-replace semantics) and the dedicated
memory-sync contract in §6.

### 11. `POST /v1/command` (legacy, v4.1-compatible)
Multipart: `audio_wav`, `language` (required, no `auto`-detect route beyond what v4.1 had),
`request_id` (optional). Response is the raw `TurnResult` shape (`request_id`, `transcript`,
`language`, `language_confidence`, `intent`, `action`, `accepted`, `confidence`, `reason`,
`latency_ms`, `offline`, `provider`). **No `user_id`/ownership concept at all** — any key
valid for any patient can drive it, but it never touches patient-scoped memory, so there is
nothing to leak across a patient boundary. `CALL_BINA`/`CALL_PRIMARY_CONTACT` are recognized
(`action` reports them) but `accepted` is always `false` here — this endpoint has no
confirmation mechanism, so a call must go through `POST /v1/conversation` instead. Prefer
the conversational endpoints for any new integration; this exists for backward compatibility
with pre-v5 clients only.

---

## 3. Audio contract

What is **actually enforced**, verified against `pipeline.py::validate_wav_bytes`:

| Property | Enforcement |
|---|---|
| Container | RIFF/WAVE header required |
| `Content-Type` | `audio/wav`, `audio/x-wav`, `audio/wave`, or `application/octet-stream` |
| Minimum size | 44 bytes, plus a `data` chunk must be present |
| Maximum size | `SMRITI_MAX_UPLOAD_BYTES` (default 10 MiB) → `413` |
| Maximum duration | `SMRITI_MAX_WAV_DURATION_S` (default 60s), best-effort/fail-open (a file this check can't parse is never rejected on that basis) → `413` |
| Sample rate / channels / bit depth | **Not enforced by this API.** Whatever the ASR provider itself accepts. 16 kHz mono 16-bit PCM is the safest choice but is not an API requirement |

Rejections: empty → `400`; not parseable as WAV or wrong content-type → `415`; over a size
or duration limit → `413`.

---

## 4. Voice job lifecycle

States: `queued → processing → {completed | failed | cancelled}`. There is no separate
`expired` job state; see `audio_expired` below for why.

```
1. Backend authenticates (x-api-key) and calls POST /v1/conversation/voice
2. VoiceBot validates the WAV (§3)
3. VoiceBot runs ASR + conversation SYNCHRONOUSLY -- response_text returns immediately
4. If speak=true, VoiceBot submits a TTS job and returns job_id, job_status=QUEUED
5. Backend/Flutter receives response_text (show it now) + job_id
6. Client polls GET /v1/voice/jobs/{job_id} every 1-2s until status is terminal
7. Client may POST /v1/voice/jobs/{job_id}/cancel at any point before a terminal state
8. On 'completed', client fetches GET /v1/audio/{audio_id} once, promptly
9. Audio ages out of retention (~15 min, SMRITI_AUDIO_RETENTION_MINUTES) -- a completed
   job's audio_expired flag turns true once that happens; the job status stays 'completed'
   (that remains historically true) but the file is gone
```

Guarantees (each independently verified, see referenced tests):

- **Bounded queue**: a full queue fails the job immediately with `error_code=QUEUE_OVERLOADED`
  rather than accepting unbounded backlog.
- **Processing deadline**: `SMRITI_VOICE_JOB_PROCESSING_DEADLINE_S` (default 180s), enforced
  lazily at every read — a stuck job is durably reported `failed`/`PROCESSING_TIMEOUT`, never
  polled forever (`test_a_job_stuck_processing_past_the_deadline_is_reported_failed`).
- **Restart recovery**: a job orphaned by a crash/restart is marked
  `failed`/`INTERRUPTED_BY_RESTART` at the next startup, never silently retried.
- **Cancellation race safety**: whichever of {cancel, worker completion} reaches the database
  first wins; the other is a no-op. A cancelled job can never later report `completed`
  (`test_cancellation_prevents_stale_completion_end_to_end`).
- **Ownership**: job/audio access outside your authorized patient set is `404`, identical to
  a nonexistent id.
- **Duplicate submission**: send `Idempotency-Key` (§5) to guarantee exactly one job for a
  retried identical request (`test_duplicate_voice_submission_creates_exactly_one_job`).

---

## 5. Idempotency contract

Optional `Idempotency-Key` header (any string, ≤128 chars) on `POST /v1/conversation` and
`POST /v1/conversation/voice`. Backed by a single shared store (`idempotency.py`), not a
per-endpoint mechanism.

| Scenario | Result |
|---|---|
| Same patient + same key + same payload (retry after timeout) | Original result returned, nothing re-executed |
| Same patient + same key + **different** payload | `409` |
| Concurrent duplicate requests, same key | Exactly one executes; the other gets `409 in_progress` or the replayed result |
| Different patient, same key string | No collision — keyed by `(user_id, key)`, never by the raw key alone |
| No key sent | Default behavior, completely unaffected — this is purely opt-in |

`POST /v1/memory/sync` doesn't need a separate idempotency key: its full-replace semantics
are already naturally idempotent (§6). Action **confirmation** (the "yes" turn) goes through
`POST /v1/conversation` like any other turn, so the same `Idempotency-Key` mechanism already
protects it — verified: `test_retried_confirmation_with_same_idempotency_key_executes_once`
(a retried "yes" never creates a reminder or places a call twice).

---

## 6. Memory sync contract

Full detail: `HANDOFF.md`, `BACKEND_APP_DEVELOPER_BACKGROUND.md`. Summary:

- **Full-snapshot, per-category replace.** Each of `family_members`/`medicines`/
  `daily_routines` you send replaces that patient's entire caregiver-synced set for that
  category; an empty array clears it; omitting a category is not the same as an empty array
  (omitted = unspecified in the request body, since these fields default to `[]` when
  absent anyway in the current schema — **send an explicit empty array to clear**, don't
  rely on omission).
- **Atomic.** One transaction; a failure (including an `external_id` collision, now
  returned as a clean `409` instead of `500`) leaves nothing partially written.
- **Provenance preserved.** Only rows with `source='caregiver' AND
  created_by='memory_sync'` are ever touched; a patient's own words, assistant notes,
  seed/import data are never affected by any sync call, verified again in this batch's
  harness (`test_full_backend_to_flutter_chain` syncs then converses and gets the synced
  fact back).
- **Opt-in revisioning.** Omit `source_revision` entirely for the original, always-applies
  behavior. Provide it (integer, ≥0) plus `schema_version` (default 1) to get:
  - older revision than currently applied → `409` (stale)
  - same revision, identical content → `200`, `status: "no_op"`
  - same revision, different content → `409` (conflict)
  - newer revision → `200`, `status: "applied"`, new revision recorded
- **Structured fields survive sync**: `chosen_time_min`/`window_start_min`/
  `window_end_min`/`days_of_week` on medicines; `timezone`/`language_code`/`display_name`/
  `external_id`/`active` (revocation, see §8) on the patient; `is_deceased`/`memory_prompt`/
  `external_id` on family members — all round-trip through `sync` → SQLite → the tool
  payloads the model sees. `active` follows the same omit-preserves rule as every other
  patient-context field: omitting it never changes the current enabled/disabled state.
- **No raw phone numbers, ever.** `phone_available: bool` only; any `phone`/`phone_number`/
  etc. field is rejected with `422` (schema `extra='forbid'`), not silently dropped.
- **Patient timezone actually affects behavior.** "What medicine do I take tonight" resolves
  "tonight" in the synced `timezone`, not a single service-wide default (see
  `tests/unit/test_per_patient_timezone.py`).

---

## 7. Error code table

### Error envelope (verified against actual responses, not assumed)

Every non-2xx response from an endpoint that takes a JSON/multipart body uses FastAPI's
default shape, unchanged:

```jsonc
// A raised HTTPException (401/403/404/409/413/415/429/500/503):
{"detail": "user_id is not authorized for this API credential"}

// A Pydantic validation failure (422) — `detail` is a LIST of field errors, not a string:
{"detail": [{"type": "missing", "loc": ["body", "message"], "msg": "Field required",
            "input": {...}}]}
```

There is **no separate `error_code`/`retryable`/`request_id` envelope wrapping these** —
introducing one now would be a breaking response-shape change for every existing client with
no demonstrated need, so this document instead makes the *existing* shape and its stable
`detail` text patterns the contract (see the table below for what each status/cause means).
The one place a genuinely machine-readable, stable code already exists is the voice-job
`error_code` field (`QUEUE_OVERLOADED`, `PROCESSING_TIMEOUT`, `INTERRUPTED_BY_RESTART`,
`NO_TTS_PROVIDER_SUPPORTS_LANGUAGE`, `TTS_UNAVAILABLE`, etc.) — use that where it exists;
fall back to HTTP status + the `detail` text for everything else. `detail` strings for a
given condition are stable (verified: `test_e2e_contract_harness.py`,
`test_patient_lifecycle.py`) but are English prose, not an enum — match on substrings if you
must branch on them, or prefer the HTTP status code, which is the actually-stable contract.

| HTTP | Meaning | Example `error_code`/cause | Retryable? | Client action |
|---|---|---|---|---|
| 400 | Malformed request / empty audio / unknown language code | — | No (fix request) | Fix and resend |
| 401 | Missing or invalid `x-api-key` | — | No | Re-authenticate |
| 403 | `user_id` unauthorized for this key, session belongs to another patient, or patient disabled | — | No | Do not retry; escalate |
| 404 | Unknown or not-yours job/audio id, unknown language code path | — | No | Treat as not-found; never distinguishable from cross-patient |
| 409 | Idempotency conflict/in-progress, memory-sync stale/conflicting revision, `external_id` collision | — | Sometimes (see message) | Use a new revision/key, or inspect the conflict |
| 413 | Upload over byte or duration limit | — | No | Reduce size/duration |
| 415 | Not a parseable WAV / wrong content-type | — | No | Fix encoding |
| 422 | Schema validation failure (bad field, raw phone number, invalid `days_of_week`/window) | — | No | Fix payload |
| 429 | Rate limit exceeded | — | Yes, with backoff | Retry after a delay |
| 500 | Internal error | — | Maybe | Log and alert; never retry blindly in a loop |
| 503 | Auth not configured / misconfigured server | — | No | Operator issue, not client-fixable |
| job `error_code=QUEUE_OVERLOADED` | TTS worker queue full | job terminal `failed` | Yes, after backoff | Re-submit the voice turn later |
| job `error_code=PROCESSING_TIMEOUT` | Synthesis exceeded the deadline | job terminal `failed` | Yes | Re-submit |
| job `error_code=INTERRUPTED_BY_RESTART` | Process restarted mid-job | job terminal `failed` | Yes | Re-submit |
| job `error_code=NO_TTS_PROVIDER_SUPPORTS_LANGUAGE` | No configured TTS for this language | job terminal `failed` | No (not a transient issue) | Show text only |
| job `error_code=TTS_UNAVAILABLE` | Provider call itself failed | job terminal `failed` | Yes, later | Show text only for now |
| `audio_expired: true` | Audio aged out of retention | — | N/A | Not an error; the text answer is still valid, audio is just gone |

No response body ever includes a stack trace, a provider credential, or a raw exception
message beyond a short descriptive string (verified: `smriti_voice/logging.py`'s redaction
applies to log output; response bodies for `500` are always the fixed string `'Memory
synchronization failed'`/`'Internal error'`-style text, never `str(exc)` directly).

---

## 8. Backend responsibilities

- Authenticate the Flutter device/app and authorize which patient(s) it may act as.
- Hold the VoiceBot `x-api-key` server-side only; proxy every VoiceBot call.
- Map your own patient identity to the `user_id` you send VoiceBot (1:1, your choice of
  format) and optionally also set `external_id` via memory sync for a second reference.
- Push memory-sync snapshots on every caregiver change; own the `source_revision` sequence
  if you adopt versioning (§6) — VoiceBot never generates one itself.
- Retry safely: use `Idempotency-Key` for conversation/voice calls you might resend.
- Scope job/audio polling to the patient session that requested it.
- Preserve the privacy boundary: caregiver access to a patient's profile does **not** imply
  access to that patient's private AI conversation history — that's a Backend-side
  permission decision, not something VoiceBot enforces or assumes for you.
- **Revoke or restore a patient via `POST /v1/memory/sync`'s `active` field** (`true` /
  `false` / omit to leave unchanged) — no separate admin endpoint exists, and none is
  needed: sending `{"user_id": "...", "active": false, ...}` (with the usual required
  arrays) disables every conversational/voice endpoint for that patient immediately;
  sending `active: true` restores it. This route is intentionally reachable even for an
  already-disabled patient, specifically so re-enabling is possible.

## 9. Flutter responsibilities

- Call your Backend, never VoiceBot directly with an end-user-held credential.
- On app open: call the welcome flow (via Backend) and display/play its result before
  entering the listening state (§10).
- Record on an explicit tap only; send one WAV per utterance.
- Show `response_text` immediately; never block the UI on TTS.
- Poll the voice job every 1-2s; stop polling the instant status is terminal.
- Call cancel if the user backs out before a job completes; discard any late result for a
  cancelled/superseded job (the job itself already enforces the correct terminal state —
  see §4 — but the UI should also stop trusting a response tied to an id it cancelled).
- Fetch audio once, promptly, after `completed`; handle `audio_expired` by falling back to
  text-only, not by erroring.
- Give medication-reminder/alarm audio priority over AI playback; never overlap the two.
- Execute an `action` **only** when `action_accepted` is `true`, and only one of the
  allow-listed strings — never raw transcript or model text (§11).

---

## 10. Welcome / first-message contract

Verified behavior (`tests/integration/test_welcome.py`,
`tests/integration/test_e2e_contract_harness.py::test_full_backend_to_flutter_chain`):

1. `POST /v1/conversation/welcome` creates or restores a session (`session_restored` tells
   you which).
2. The greeting is **entirely deterministic** — a fixed per-language template, never an LLM
   call (confirmed: zero calls reach the configured LLM provider for this endpoint).
3. It is **not** represented as a user turn — nothing is written to conversation history, so
   `session.pending`/history are unaffected.
4. The user's actual next message (via `POST /v1/conversation` or
   `POST /v1/conversation/voice`, same `session_id`) is unaffected — it is turn #1, not #2.
5. `speak: true` reuses the exact same async TTS job pipeline as the voice endpoint — poll
   the same way.
6. Calling it again for the same `session_id` is side-effect-free and returns the same
   deterministic text (`session_restored: true`).

---

## 11. Action proposal semantics

```
LLM proposes a tool call
  -> schema validation (extra fields rejected)
  -> deterministic safety screen
  -> authorization (allow-list + patient ownership)
  -> confirmation, if the tool requires it (spoken/typed explicit "yes" only)
  -> handler executes
  -> typed result returned
```

`action_accepted: true` means the deterministic gate authorized it — it is **not** proof an
external side effect (a phone call, a device alarm) actually happened, because for
call/reminder actions **no real executor exists in this repository today**:

| Tool/action | Requires confirmation? | What actually happens |
|---|---|---|
| `open_app` (`OPEN_PLAY`/`OPEN_MY_PEOPLE`/`OPEN_TODAY`/`OPEN_MEDICINE`/`HELP`/`STOP`) | No | Returns a navigation signal; Flutter opens the screen. VoiceBot does nothing further. |
| `call_family_member` (`CALL_PRIMARY_CONTACT`/`CALL_BINA`) | **Yes** | Returns `{'called': True, 'name': ..., 'relation': ...}` — a *proposal*, not a placed call. No telephony executor exists in VoiceBot. Your Backend/Flutter must decide (device dialer vs. an authorized backend calling flow — these are different implementations) and must treat this response as "confirmed intent to call," not "call completed." |
| `create_reminder` | **Yes** | Writes a SQLite row only. No device alarm is scheduled. Real scheduling is Backend/Flutter's responsibility if/when you build it. |
| `start_game` | **Yes** | Returns a navigation signal; does not fabricate a completed game session. |
| Unknown tool name / sensitive tool (`change_medication`, `transfer_money`, `delete_record`, `call_number`) | N/A | Always fails closed; never executes, never advertised to the model. |

Unknown actions fail closed (verified: `test_unknown_tool_name_fails_closed`). Model output,
transcript text, and memory text are never directly executable — only the deterministic gate
above authorizes anything.

---

## 12. Reproducibility & operations

See `SECURITY.md`'s "Operational readiness" section for full detail (migrations, restart
behavior, health/readiness semantics, credential rotation). Highlights for integration
planning:

- Dependencies are minimum-pinned (`requirements.txt` uses `>=`), not exact-pinned — a
  genuinely open gap, not something silently claimed fixed.
- Backup: `python tools/backup_db.py backup` (uses SQLite's online-backup API, safe against
  a live database — never a torn copy). The audio cache directory is a disposable,
  self-expiring cache, not backup-critical.
- `GET /v1/health` is a liveness check; it returns `ok` regardless of which optional cloud
  providers are configured.
- This is a **pilot deployment** (Windows host, Tailscale Funnel, one Uvicorn worker, SQLite)
  — not presented as production-grade availability.

---

## 13. Language capability matrix

See `HANDOFF.md`'s language table for the full, verified-against-source matrix (Hindi,
Assamese, Meiteilon, Khasi, Mizo, English). Read `GET /v1/languages` live for current state
— this document is a planning snapshot, not a runtime source of truth.

---

## 14. Flutter client state machine

A concrete state machine for the app-open-to-audio-playback flow, using only fields that
actually exist in the response schemas above. This describes the *client's* states, not
VoiceBot's turn/job states (§4) — map between them as shown.

```
IDLE
  -> WELCOME_LOADING      (POST /v1/conversation/welcome)
  -> LISTENING            (welcome received; mic available on explicit tap)
  -> SENDING              (user tapped mic, recorded, POST /v1/conversation/voice sent)
  -> RESPONSE_READY       (response_text received -- show it immediately, regardless of TTS)
  -> AUDIO_QUEUED         (if speak was requested and job_id is non-null; poll job status)
  -> AUDIO_PLAYING        (job status == 'completed', audio fetched and playing)
  -> LISTENING            (playback finished; ready for the next utterance)
```

Failure/exception paths from each state:

```
SENDING          -> NETWORK_ERROR     (the HTTP call itself failed/timed out; show a retry
                                        affordance, do not assume anything executed)
AUDIO_QUEUED     -> FAILED            (job status == 'failed'; response_text from
                                        RESPONSE_READY is still valid and already shown --
                                        text never depends on TTS succeeding)
AUDIO_QUEUED     -> CANCELLED         (user backed out / navigated away; call
                                        POST /v1/voice/jobs/{id}/cancel, then discard the
                                        job_id -- do not act on any later poll result for it)
AUDIO_PLAYING    -> AUDIO_EXPIRED     (job status == 'completed' but audio_expired == true,
                                        or a GET /v1/audio/{id} fetch itself returns 404 after
                                        completion; fall back to text-only, this is not an
                                        error to alarm the user about)
```

Non-negotiable client rules (all already implied by fields in §2, restated explicitly):

1. **Never hold the VoiceBot `x-api-key`** — the client talks to its own Backend, which
   proxies to VoiceBot.
2. **Never execute raw model/transcript text as a command.** Only act on `action` when
   `action_accepted == true`, and only on the allow-listed strings (§11).
3. **Show `response_text` the moment it arrives** — never block the UI on `job_status`.
4. **Stop polling the instant `status` is terminal** (`completed`/`failed`/`cancelled`) —
   polling a terminal job forever wastes a request every 1–2s for no reason.
5. **Cancel, don't just stop polling**, when the user discards a request in flight — an
   un-cancelled job keeps synthesizing on the server for no one.
6. **Ignore a poll result for a `job_id` you've already cancelled or superseded** — the
   server-side race safety (§4) guarantees the *job's* state is correct, but the client's
   own UI state should independently never resurrect a result it already gave up on.
7. **Give medication-reminder/alarm audio priority over AI playback** — this is
   Flutter-owned (VoiceBot has no concept of device alarms at all, see §11's "do not
   implement" list).
8. **Handle `audio_expired` as a normal, expected outcome**, not an error state — text is
   already showing; there is nothing to alarm the user about.
