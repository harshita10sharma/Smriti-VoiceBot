# VoiceBot Integration Guide

**Audience:** the Backend and Flutter developers integrating against this VoiceBot
service. You should be able to build against this document alone, without reading
VoiceBot's source code. Where a claim needs deeper evidence, it links to the exact
file/test that proves it.

**Companion documents:**
- [`docs/BACKEND_VOICEBOT_INTEGRATION.md`](BACKEND_VOICEBOT_INTEGRATION.md) — step-by-step for the Backend developer.
- [`docs/FLUTTER_VOICEBOT_INTEGRATION.md`](FLUTTER_VOICEBOT_INTEGRATION.md) — step-by-step for the Flutter developer.
- [`../INTEGRATION_CONTRACT.md`](../INTEGRATION_CONTRACT.md) — the terse, verified reference this guide expands on.
- [`docs/integration/`](integration/) — machine-readable exports: `openapi.json`, `voicebot-memory-schema.json`, `voicebot-action-schema.json`, `voicebot-language-matrix.json`, `voicebot-error-catalog.json`, all generated from the actual current implementation.
- [`../TESTING.md`](../TESTING.md) — what the deterministic test mode proves and does not prove.
- [`../docs/RELEASE_ACCEPTANCE.md`](RELEASE_ACCEPTANCE.md) — the final status matrix, evidence-cited.

---

## 1. Architecture

```
Flutter (patient tablet)
    |
    | HTTPS, its OWN Backend session token — never a VoiceBot credential
    v
Backend Gateway  (Supabase-authenticated; NOT YET BUILT — see PROVISIONING_DESIGN.md)
    |
    | x-api-key (server-side only)
    v
VoiceBot API  (this repository)
    |
    v
ASR -> Conversation / Safety / Memory / Actions -> TTS / Voice Jobs
```

**Flutter does not call VoiceBot directly in production.** Every request Flutter needs
goes through the Backend, which holds the VoiceBot credential and proxies the call. If
your team ever changes this (e.g. a pilot that has Flutter call VoiceBot directly), that
is an explicit architecture decision to make jointly — not something to infer from this
document, and not the documented/tested configuration.

## 2. Authentication

Every protected VoiceBot endpoint requires:

```
x-api-key: <credential>
```

- **The Backend holds this credential server-side only.** Flutter never receives it, the
  browser never receives it, and it is never present in any VoiceBot response body
  (verified: `tests/unit/test_privacy_data_minimization.py::test_401_response_never_echoes_the_submitted_key`).
- Provider credentials (`GROQ_API_KEY`, `SARVAM_API_KEY`, `HF_TOKEN`, etc.) never leave
  VoiceBot at all — not to the Backend, not to Flutter.
- Missing/invalid key → `401`. Unauthorized `user_id` for a valid key → `403`.
- Two configuration modes exist server-side (`SMRITI_API_KEY`+`SMRITI_AUTH_USER_ID` for a
  single fixed patient, or `SMRITI_API_KEYS` — a JSON map — for one key per patient or one
  backend key authorized for many patients). Which mode is active is a VoiceBot deployment
  decision; either way, the Backend only ever sends `x-api-key` and a `user_id` it is
  authorized for.

## 3. Patient identity

```
Supabase patients.id  (a UUID, the real system of record)
    -> the Backend's own choice of external_patient_id (may be the same UUID, 1:1)
    -> VoiceBot's user_id (any string matching [A-Za-z0-9\-_.]{1,64} — a UUID fits directly)
```

- **`user_id` is immutable and one-to-one.** Send the same identifier for this patient on
  every request, forever. VoiceBot's memory, sessions, jobs, and audio are all scoped to
  it, never to the credential used to reach it (verified:
  `tests/integration/test_credential_rotation.py` — rotating which key maps to a patient
  never loses that patient's memory or session ownership).
- **Provisioning is automatic and separate from authorization.** The first time VoiceBot
  sees a `user_id` your key authorizes — either a memory sync or a real conversation turn —
  it silently creates a bare patient row. There is no separate "create patient" call to
  make. Authorization is checked from the key configuration alone, never from whether that
  row exists yet.
- **Disable/re-enable**: `POST /v1/memory/sync` with `{"user_id": "...", "active": false, ...}`
  disables every conversational/voice/job/audio endpoint for that patient immediately;
  `active: true` re-enables it. `active` omitted leaves the current state untouched. This
  route stays reachable even for a disabled patient, specifically so re-enabling is
  possible. Re-enabling never resets memory (verified:
  `tests/integration/test_patient_lifecycle.py`).
- **Credential rotation**: change which key maps to this `user_id` (an env var change +
  restart today — see `PROVISIONING_DESIGN.md` for the future scalable mechanism). The old
  credential is rejected immediately; the new one works for the same patient identity with
  all memory/sessions intact (verified live, `tests/integration/test_credential_rotation.py`).

## 4. Endpoint catalogue (CURRENT PRODUCTION CONTRACT)

Every endpoint below exists in the current codebase and is exercised by a passing test.
Nothing in this table is proposed or aspirational.

| # | Method & path | Auth | Patient field | Caller |
|---|---|---|---|---|
| 1 | `GET /v1/health` | none | — | Uptime monitor, Backend |
| 2 | `GET /v1/languages` | none | — | Backend (capability planning) |
| 3 | `GET /v1/languages/{code}` | none | — | Backend |
| 4 | `POST /v1/conversation` | key | `user_id` (body) | Backend (proxying text turns) |
| 5 | `POST /v1/conversation/voice` | key | `user_id` (form) | Backend (proxying voice turns) |
| 6 | `POST /v1/conversation/welcome` | key | `user_id` (body) | Backend (proxying app-open) |
| 7 | `GET /v1/voice/jobs/{job_id}` | key | resolved from job | Backend/Flutter (poll) |
| 8 | `POST /v1/voice/jobs/{job_id}/cancel` | key | resolved from job | Backend/Flutter |
| 9 | `GET /v1/audio/{audio_id}` | key | resolved from job | Backend/Flutter |
| 10 | `POST /v1/memory/sync` | key | `user_id` (body) | **Backend only** |
| 11 | `POST /v1/command` | key | none (legacy) | Backend, v4.1-compatible clients only |
| 12 | `GET /v1/tools` | key | — | Backend (introspection/debugging) |

**PROPOSED/OPTIONAL FUTURE CONTRACT** (not implemented, do not build against these paths):
a dedicated provisioning/admin endpoint (today provisioning is implicit — see §3), a
controlled-calling execution-result callback (§15 below), a conversational-reminder
scheduling callback (§16 below). None of these exist in code today.

Full exact request/response shapes: [`docs/integration/openapi.json`](integration/openapi.json)
(auto-generated from the live app, always in sync with the actual routes).

## 5. Example requests/responses

These are real, current shapes — not illustrative approximations. See
`../integration/fixtures/` for the canonical JSON files these are drawn from.

**Health** — `GET /v1/health` (no auth):
```json
{"status": "ok", "version": "5.0.0", "connectivity": {"online": true, "...": "..."},
 "providers": {"...": "..."}, "capabilities": {"...": "..."},
 "languages": {"configured": 15, "...": "..."}, "tools": {"registered": 21},
 "configuration": {"...": "..."}}
```

**Languages** — `GET /v1/languages/hin`:
```json
{"code": "hin", "name": "Hindi", "script": "Devanagari",
 "asr_provider_online": "sarvam", "asr_online": true, "asr_offline": true,
 "llm_support": true, "tts_provider": "sarvam", "tts_online": true,
 "status": "NOT_YET_TESTED", "validated": false, "known_limitations": [...]}
```

**Welcome** — `POST /v1/conversation/welcome`:
```json
// request
{"user_id": "elder-1", "session_id": null, "language": "hin", "speak": false}
// response
{"request_id": "...", "session_id": "...",
 "response_text": "नमस्ते, ...। मैं आपके साथ हूँ। ...",
 "language": "hin", "kind": "WELCOME", "session_restored": false,
 "job_id": null, "job_status": "NOT_REQUESTED", "audio_id": null, "audio_url": null,
 "audio_available": false, "audio_unavailable_reason": "TTS_NOT_REQUESTED",
 "tts_provider": null}
```
Note `language: "hin"` in the response even though the request could equally have said
`"hi"` — see §12, language codes are normalized once at the boundary.

**Text conversation** — `POST /v1/conversation`:
```json
// request
{"user_id": "elder-1", "session_id": null, "message": "What is my daughter's name?",
 "language": "eng"}
// response
{"request_id": "...", "session_id": "...", "response_text": "...", "language": "eng",
 "language_confidence": 0.0, "kind": "MEMORY", "action": "NO_ACTION",
 "action_accepted": false, "tool_calls": [...], "tool_results": [...], "safety": null,
 "requires_confirmation": false,
 "metadata": {"request_id": "...", "session_id": "...", "kind": "MEMORY",
              "execution_mode": "ONLINE_PRIMARY", "offline": false, "fallback_used": false,
              "llm_provider": "groq", "llm_latency_ms": 616, "tool_latency_ms": 4,
              "total_latency_ms": 640, "error_code": null, "topic": "personal",
              "action_id": null}}
```

**Voice** — `POST /v1/conversation/voice` (multipart: `audio_wav`, `user_id`,
`session_id` optional, `language` optional, `speak` default `true`) → same shape as
`ConversationResponse` plus `transcript`, `job_id`, `job_status`,
`audio_id`/`audio_url` (`null` here — TTS is async), `audio_available: false`,
`audio_unavailable_reason`, `tts_provider: null`.

**Job polling** — `GET /v1/voice/jobs/{job_id}`:
```json
{"job_id": "...", "status": "completed", "language": "eng", "audio_id": "...",
 "audio_url": "/v1/audio/...", "tts_provider": "sarvam", "error_code": null,
 "audio_expired": false}
```

**Cancellation** — `POST /v1/voice/jobs/{job_id}/cancel`:
```json
{"job_id": "...", "status": "cancelled", "cancelled": true}
```

**Audio retrieval** — `GET /v1/audio/{audio_id}` → raw `audio/wav` bytes, fetch once,
promptly.

**Memory sync** — `POST /v1/memory/sync`: see §11 and
[`docs/integration/voicebot-memory-schema.json`](integration/voicebot-memory-schema.json)
for the exact schema.

**Command (legacy)** — `POST /v1/command`: multipart `audio_wav`, `language` (required),
`request_id` (optional) → `{"request_id", "transcript", "language", "language_confidence",
"intent", "action", "accepted", "confidence", "reason", "latency_ms", "offline",
"provider"}`. No `user_id`/ownership concept. Prefer the conversational endpoints for any
new integration.

## 6. Error catalogue

See [`docs/integration/voicebot-error-catalog.json`](integration/voicebot-error-catalog.json)
for the machine-readable version. Summary:

| HTTP | Meaning |
|---|---|
| 400 | Malformed request / empty audio / unknown language code |
| 401 | Missing or invalid `x-api-key` |
| 403 | Unauthorized `user_id`, cross-patient session, or disabled patient |
| 404 | Unknown/not-yours job or audio id, unknown language code path — indistinguishable from cross-patient by design |
| 409 | Idempotency conflict/in-progress, memory-sync stale/conflicting revision, `external_id` collision |
| 413 | Upload over byte/duration limit |
| 415 | Not a parseable WAV / wrong content-type |
| 422 | Schema validation failure |
| 429 | Rate limit exceeded (retry with backoff) |
| 500 | Internal error |
| 503 | Server auth misconfigured |

An **unknown patient is not an error** — it is auto-provisioned on first contact. A
**disabled patient** is `403`, not `404` — the distinction matters: `404` means "this id
doesn't exist or isn't yours," `403` means "this identity exists and is yours, but is
blocked."

## 7. Session rules

- A session belongs to exactly one `user_id`, enforced from a persistent SQLite row, not
  in-memory state — survives a restart (verified:
  `tests/integration/test_persistent_sessions.py`).
- Send `session_id: null` on the very first call; VoiceBot mints one and returns it.
  Reuse the returned `session_id` for every subsequent turn in the same conversation.
- A `session_id` belonging to a different patient → `403`, identical behavior on both
  `POST /v1/conversation` and `POST /v1/conversation/voice` (this was a real, now-fixed
  inconsistency — see `CHANGELOG.md`, 2026-09-13).
- **Concurrency**: two requests naming the same `session_id` at the same time (a
  double-tap, a client retry racing the original) are serialized per-session so neither's
  state is silently lost — different patients' sessions are never blocked by each other
  (verified: `tests/integration/test_concurrent_sessions.py`, 7 scenarios including
  simultaneous confirmations, cross-patient hijack attempts, and a simulated restart).
- **Expiry**: a session idles out after `SMRITI_MAX_SESSION_IDLE_MINUTES` (default 30) of
  inactivity; the next request with that `session_id` transparently starts a fresh session
  rather than resurrecting a stale one (a stale pending confirmation must never come back
  to life).
- **Confirmation lifecycle**: see §13.

## 8. Voice/audio contract

**Input (what Flutter must record and upload):**

| Property | Requirement |
|---|---|
| Container | RIFF/WAVE |
| `Content-Type` | `audio/wav`, `audio/x-wav`, `audio/wave`, or `application/octet-stream` |
| Minimum size | 44 bytes, plus a `data` chunk present |
| Maximum size | `SMRITI_MAX_UPLOAD_BYTES` (default 10 MiB) → `413` above it |
| Maximum duration | `SMRITI_MAX_WAV_DURATION_S` (default 60s); best-effort — a file the duration check can't parse is never rejected on that basis alone → `413` if confirmed over |
| Sample rate / channels / bit depth | **Not enforced by the API.** 16 kHz mono 16-bit PCM is the safest, most broadly ASR-compatible choice, but is not a hard requirement |
| Empty payload | `400` |
| Not parseable as WAV / wrong content-type | `415` |

**Output (what Flutter fetches and plays):** raw `audio/wav` bytes from
`GET /v1/audio/{audio_id}`, fetched once and promptly — the retention window
(`SMRITI_AUDIO_RETENTION_MINUTES`, default ~15 min) is short by design. After that window,
the job's `audio_expired` becomes `true` (job `status` remains `completed` — this is not
a new job state, see §9) or the fetch itself returns `404`. Either way, treat it as a
normal outcome: fall back to text-only, never surface it as an error.

## 9. Voice job lifecycle

```
queued -> processing -> { completed | failed | cancelled }
```

- No separate `expired` job status exists. A completed job whose audio has aged out is
  reported via `audio_expired: true` on the SAME `completed` status — this is deliberate,
  not an oversight (see §8).
- **Bounded queue**: a full queue fails the job immediately with `error_code=QUEUE_OVERLOADED`.
- **Processing deadline**: a job stuck past `SMRITI_VOICE_JOB_PROCESSING_DEADLINE_S`
  (default 180s) is durably reported `failed`/`PROCESSING_TIMEOUT` — never polled forever.
- **Restart recovery**: a job orphaned by a crash/restart is marked
  `failed`/`INTERRUPTED_BY_RESTART` at the next startup.
- **Cancellation race safety**: whichever of {cancel, worker completion} reaches the
  database first wins; a cancelled job can never later report `completed`.
- **Ownership**: job/audio access outside your authorized patient set is `404`.
- **Duplicate submission**: an `Idempotency-Key` guarantees exactly one job for a retried
  identical request.

## 10. Idempotency

Optional `X-Idempotency-Key` header (any string, ≤128 chars) on `POST /v1/conversation`
and `POST /v1/conversation/voice`. HTTP headers are case-insensitive — send it however you
like, it will be read correctly.

| Scenario | Result |
|---|---|
| Same patient + same key + same payload | Original result returned, nothing re-executed |
| Same patient + same key + different payload | `409` |
| Concurrent duplicate requests, same key | Exactly one executes; the other gets `409 in_progress` or the replayed result |
| Different patient, same key string | No collision — keyed by `(user_id, key)`, never the raw key alone |
| No key sent | Default, unaffected behavior — purely opt-in |

`POST /v1/memory/sync` doesn't need this — its full-replace-with-revision semantics (§11)
are already naturally idempotent.

## 11. Memory synchronization

Full schema: [`docs/integration/voicebot-memory-schema.json`](integration/voicebot-memory-schema.json).
Field-by-field mapping guidance for a real Supabase-shaped source: `../INTEGRATION_CONTRACT.md` §6.

- **Full-snapshot, per-category replace.** Send the patient's entire current
  `family_members`/`medicines`/`daily_routines` set every time; an empty array clears that
  category, omitting a key leaves it unspecified (send explicit empty arrays to clear).
- **Atomic.** One transaction; any failure leaves nothing partially written.
- **Opt-in revisioning.** Omit `source_revision` for the original always-applies behavior.
  Provide it (int ≥0) + `schema_version` (default 1) for: older revision → `409` stale;
  same revision + identical content → `200` `no_op`; same revision + different content →
  `409` conflict; newer revision → `200` `applied`.
- **The revision hash covers every field in the snapshot, not just the three arrays** —
  changing only `timezone`/`display_name`/`language_code`/`active` under the same
  `source_revision` is correctly a conflict, never a silently-ignored no-op (verified:
  `tests/integration/test_memory_sync_versioning.py`).
- **No raw phone numbers, ever** — `phone_available: bool` only; a `phone`/`phone_number`
  field is `422`.
- Structured medicine fields (`chosen_time_min`/`window_start_min`/`window_end_min`,
  integers 0–1439; `days_of_week`, comma-separated ISO weekdays, Monday=1..Sunday=7;
  non-wrapping window: `window_start_min <= chosen_time_min <= window_end_min`) round-trip
  exactly as sent.
- Routine `time` is a `"HH:MM"` 24-hour string (not minutes) — see `../INTEGRATION_CONTRACT.md`
  §6 for the exact Backend-side conversion if your source stores minutes.

## 12. Language matrix

Machine-readable, generated from the live registry:
[`docs/integration/voicebot-language-matrix.json`](integration/voicebot-language-matrix.json).
Also live at runtime: `GET /v1/languages`.

| Frontend code | VoiceBot code | ASR | LLM | TTS | Status | Notes |
|---|---|---|---|---|---|---|
| `en` | `eng` | ✓ (Sarvam) | ✓ | ✓ (Sarvam) | NOT_YET_TESTED | Speakable today |
| `hi` | `hin` | ✓ (Sarvam) | ✓ | ✓ (Sarvam) | NOT_YET_TESTED | Speakable today |
| (Assamese) | `asm` | ✓ (Sarvam) | ✓ | ✗ | NOT_YET_TESTED | No configured TTS voice |
| `mni` | `mni` | ✓ (Sarvam) | ✗ | ✗ | NOT_YET_TESTED | ASR only; **never mapped to Mongolian** (a real bug found and fixed — see `CHANGELOG.md`) |
| `kha` | `kha` | ✗ | ✗ | ✗ | BENCHMARK_ONLY | Nothing works online for Khasi today |
| `lus` | `lus` | ✗ | ✗ | ✗ | BENCHMARK_ONLY | Nothing works online for Mizo today |

**`validated: false` for every language in this table, without exception.** No native-speaker
quality validation has been performed on any language, including English — `GET /v1/languages`
reports this honestly; do not present any language to end users as quality-validated based
on this repository alone.

**Codes are normalized once, at the API boundary.** Send either form (`hi` or `hin`) and
VoiceBot resolves it to the canonical form before any downstream lookup — this was a real
bug (silent English fallback for `hi`/`as`) found and fixed; see `CHANGELOG.md`.

## 13. Action semantics

```
LLM proposes a tool call
  -> schema validation (extra fields rejected)
  -> deterministic safety screen
  -> authorization (allow-list + patient ownership)
  -> confirmation, if required (explicit "yes" only, spoken/typed)
  -> handler executes
  -> typed result returned
```

- `action_accepted: true` means the deterministic gate authorized it — **not** proof an
  external side effect happened. No real call/alarm executor exists in this repository
  today (§15, §16).
- `metadata.action_id` (new, additive) is a stable id for one specific proposal instance,
  present on the proposing turn and the resolving turn, `null` otherwise — use it to
  correlate a "yes"/"no" with the exact proposal it resolves rather than inferring "the one
  pending action" from `session_id` alone.
- Unknown actions fail closed. Neither raw transcript nor model text is ever directly
  executable.
- Confirmation states, mapped onto the real fields (no separate state-name field exists —
  derive it from these):

| State | How to detect it |
|---|---|
| PROPOSED / AWAITING_CONFIRMATION | `kind == "CONFIRMATION"`, `requires_confirmation == true`, `action_accepted == false` |
| AUTHORIZED_FOR_EXECUTION / COMPLETED | `action_accepted == true` |
| CANCELLED | `kind == "CONFIRMATION"`, `action_accepted == false`, `requires_confirmation == false` (an explicit "no") |
| EXPIRED | A later "yes" against an old proposal returns `action_accepted == false` — the confirmation window (`~3 minutes`, `PendingConfirmation.expires_in_turns`) had already closed |
| FAILED | `tool_results[].ok == false` / `error_code` set |

## 14. Navigation integration

| Action | Flutter should navigate to |
|---|---|
| `OPEN_PLAY` | Games |
| `OPEN_MY_PEOPLE` | People |
| `OPEN_TODAY` | Today/routine |
| `OPEN_MEDICINE` | Medicines |
| `HELP` | (no navigation — spoken help) |
| `STOP` | (no navigation — stop current activity) |

**VoiceBot never navigates Flutter itself.** It returns `action` + `action_accepted: true`;
Flutter maps the string to its own real screen. Opening "Games" via this path must not
fabricate a completed game session or synthetic trial events — VoiceBot has no opinion
about what happens inside the game.

## 15. Controlled calling — OPTIONAL, currently gated off

No telephony executor exists in this repository. `call_family_member` returns
`{'called': True, 'name': ..., 'relation': ...}` — a **confirmed intent to call**, never a
placed call. To enable real calling, jointly build: an opaque trusted-contact reference
(not a raw phone number the model can see or generate), an authenticated executor, an
execution-result callback tied to `action_id`, duplicate-execution prevention, and honest
cancellation/failure reporting. `phone_available` on a family member is a capability hint
only — it does not by itself authorize a call, and (per the real Supabase-shaped schema
this was verified against) most patient schemas have no phone field on a person at all;
the only real callable contact in such a schema is the patient-level escalation contact,
a different concept entirely. See `../INTEGRATION_CONTRACT.md` §9.

## 16. Conversational reminders — OPTIONAL, currently gated off

`create_reminder` writes a SQLite row only — no device alarm is scheduled. To enable real
reminders, Backend/Flutter must build the actual authorized scheduling and delivery
mechanism; VoiceBot's role stays limited to producing a structured proposal
(`reminder_text`, `patient_timezone`, a resolved date/time, confirmation state) for you to
act on. Medication changes are never delegated to a conversational reminder — they remain
caregiver-only, through memory sync. See `../INTEGRATION_CONTRACT.md` §10.

## 17. Offline/degraded behavior

| Case | What happens |
|---|---|
| VoiceBot reachable, general LLM unavailable | Deterministic fallback answers what it can from saved data; a genuinely unanswerable question gets a truthful "I don't have that" rather than a guess |
| VoiceBot service unreachable | Backend's problem to handle (timeout/retry policy) — VoiceBot cannot respond to a call that never arrives |
| Tablet has no network | Flutter's existing local reminders/games/downloaded media continue independently; this is unrelated to VoiceBot |
| Requested-language ASR/TTS unavailable | `NO_TTS_PROVIDER_SUPPORTS_LANGUAGE` (job) or the language stays text-only; never silently substituted for another language |

**Remote deterministic fallback is not offline conversational AI.** VoiceBot is a remote
service; nothing here runs on the tablet.

## 18. Privacy

Never logged, anywhere in this repository, by design (verified:
`tests/unit/test_privacy_data_minimization.py`): API keys/`x-api-key` values,
`Authorization` headers, raw phone numbers, raw audio, full transcripts, full memory
payloads, signed URLs, bearer credentials. A `500` response body is always a fixed safe
string, never `str(exc)` or a stack trace.

## 19. Retry strategy

- **Safe to retry freely**: `GET /v1/health`, `GET /v1/languages*`, `GET /v1/voice/jobs/{id}`
  (polling), `GET /v1/audio/{id}` (until it 404s from expiry).
- **Must use `X-Idempotency-Key` to retry safely**: `POST /v1/conversation`,
  `POST /v1/conversation/voice` — these have side effects (LLM calls, tool execution,
  history writes); blind retries without the key can double-execute a confirmed action.
- **`POST /v1/memory/sync`** is naturally idempotent via full-replace + revision semantics
  (§11) — no separate key needed.

## 20. Deployment

VoiceBot's `x-api-key`/`SMRITI_API_KEYS`/provider-credential model maps directly onto
whatever secret store your platform uses (environment variables either way). See
`../STAGING_READINESS.md`'s "Deploying on Azure specifically" section for concrete
considerations (persistent storage for SQLite, the current single-instance constraint,
Key Vault/App Settings for secrets). **No secrets are ever written in this document or any
file in this repository.**

## 21. Integration checklist

**Backend:**
- [ ] Patient mapping (`patients.id` → `user_id`)
- [ ] Server-side VoiceBot credential (`x-api-key`), never forwarded to Flutter
- [ ] Authorization enforced before proxying
- [ ] Provisioning understood as automatic (no separate call needed)
- [ ] Memory snapshot builder (whitelisted fields only — see §11)
- [ ] `source_revision`/`schema_version` adopted if versioning is wanted
- [ ] Language code mapping (`hi`/`as`/`mni`/`kha`/`lus`/`en` → VoiceBot forms, or send as-is — both work, see §12)
- [ ] Job proxy (poll + cancel)
- [ ] Audio proxy (fetch-once semantics preserved)
- [ ] Retry logic, with `X-Idempotency-Key` on side-effecting calls
- [ ] Error mapping (§6) surfaced sensibly to Flutter
- [ ] Revocation wired to `active` in memory sync
- [ ] Feature flag for pilot rollout
- [ ] Staging fixtures (`../integration/fixtures/`)
- [ ] E2E tests against a real running VoiceBot instance

**Flutter:**
- [ ] Session id persisted per conversation, discarded on re-pairing
- [ ] Recording matches the audio contract (§8)
- [ ] WAV upload via the Backend proxy, never VoiceBot directly
- [ ] `response_text` shown immediately, never blocked on TTS
- [ ] Job polling every 1–2s, stopped the instant status is terminal
- [ ] Audio playback, fetched once promptly after `completed`
- [ ] Cancel called (not just stop-polling) when the user backs out
- [ ] Stale/superseded poll results discarded client-side
- [ ] Alarm/medication audio given priority over AI playback
- [ ] Navigation actions mapped per §14
- [ ] Action executed only when `action_accepted == true`
- [ ] Calling/reminder actions left disabled (§15, §16)
- [ ] Network failure handled without assuming anything executed
- [ ] Local reminders/games/media unaffected by VoiceBot reachability
- [ ] No VoiceBot credential, no provider credential, anywhere in the app

## 22. Acceptance test matrix

| Layer | Who runs it | Where |
|---|---|---|
| VoiceBot-only | This repository's CI | `pytest -q` (593 tests as of this release) |
| Backend integration | Backend team, against a real VoiceBot instance | Not yet built — see `PROVISIONING_DESIGN.md` |
| Flutter integration | Flutter team, against the Backend gateway | Not yet built |
| Joint physical-device | All teams together | External — see `../STAGING_READINESS.md` |

See `docs/RELEASE_ACCEPTANCE.md` for the full, evidence-cited status of every item.
