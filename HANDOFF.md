# Smriti VoiceBot handoff

Practical integration handoff for the Backend and Flutter teams. Current live deployment:
`https://15-206-144-216.nip.io` (see `docs/AWS_DEPLOYMENT.md`). For a narrative
walkthrough, use `docs/VOICEBOT_INTEGRATION_GUIDE.md`; this document is the detail-level
reference, organized A–Q. **`INTEGRATION_CONTRACT.md` remains the authoritative
behavioral/ownership contract** — this document is supporting detail, not a competing
authority.

**A.** Backend responsibilities · **B.** Flutter responsibilities · **C.** VoiceBot
responsibilities · **D.** Authentication · **E.** Patient identity · **F.** Memory
synchronization · **G.** Text request · **H.** Voice request · **I.** TTS polling ·
**J.** Cancellation · **K.** Audio retrieval · **L.** Confirmation flow · **M.** Action
execution boundary · **N.** Idempotency · **O.** Error handling · **P.** Security rules ·
**Q.** [Final E2E checklist](#q-final-e2e-checklist)

## A. Backend responsibilities

Flutter/device authentication, Supabase patient authorization, mapping the Supabase
patient UUID to VoiceBot's `user_id` (no translation needed — send the UUID directly),
holding the VoiceBot `x-api-key` credential server-side, calling VoiceBot on the app's
behalf, pushing caregiver data into VoiceBot via `POST /v1/memory/sync` with
revision/retry handling, proxying/scoping job and audio access, whitelisting which fields
get synced (never forward Supabase's complete private content response), rollout/feature
flags. **Must not** duplicate VoiceBot's ASR/LLM/TTS/safety/memory logic.

## B. Flutter responsibilities

Recording, calling the Backend (never VoiceBot directly with a secret), displaying
`response_text`, polling/cancelling voice jobs through the Backend proxy, playback,
alarm/media priority over AI audio, offline/network-loss behavior, re-pairing, rejecting
obsolete results (a late job response for a session the user has already left).

## C. VoiceBot responsibilities

ASR, conversation routing, personal memory, deterministic safety, prompt-injection
handling, action proposal/validation/confirmation, TTS, voice jobs, temporary audio,
language capability reporting, its own API/deployment security. See `ARCHITECTURE.md`.

## D. Authentication

Every endpoint except `GET /v1/health` and `GET /v1/languages*` requires an `x-api-key`
header — **never `Authorization: Bearer`**. The credential is server-side only: Backend
holds it and calls VoiceBot on the app's behalf; it must never reach Flutter or a browser.
Missing/invalid key → `401`. No key configured on the server at all → `503` (fails closed,
never opens access). A caller-supplied `user_id` that isn't authorized for the presented
key → `403` — see **E** below. Generate a credential with
`python -m smriti_voice.api.generate_key`.

## F. Memory synchronization

See `API_INTEGRATION.md`'s "Endpoint: caregiver memory sync" section for the full request/
response schema, including the optional `source_revision`/`schema_version` fields that
enable stale-revision rejection, same-revision-conflict rejection, and idempotent
identical-revision replay (all three verified live against the deployment above).

---

## Historical: v4.1 on-device bundling notes

The following describes the **original v4.1 on-device deployment model** (the Python
service bundled directly on the device, no HTTP API, no AWS deployment) — kept for
historical reference, not the current integration path. The current path is the HTTP API
documented throughout this file and in `API_INTEGRATION.md`.

1. Bundle the Python service on the device/server OR call the HTTP service.
2. Send `language` explicitly from the caregiver profile. Do not silently guess a language.
3. On the red microphone tap, capture one utterance and send one WAV request.
4. If `accepted=false`, do not execute anything. Show the large touch fallback.
5. Execute only these action strings: `OPEN_PLAY`, `OPEN_MY_PEOPLE`, `OPEN_TODAY`, `CALL_BINA`, `OPEN_MEDICINE`, `HELP`, `STOP`.
   `CALL_BINA` is recognized by `/v1/command` but **never returns `accepted=true` there** —
   this endpoint has no confirmation step, so placing a call always requires the
   `/v1/conversation` flow below (which does confirm). This is intentional.
6. Never expose an arbitrary transcript as an executable command.
7. Keep medicine editing outside voice commands.

`SMRITI_PROVISIONING` unset/0 plus a pre-installed model pack was the original offline,
no-HTTP-API deployment mode. It is not how the current live deployment runs.

Language phrases are edited in `language_packs/commands_<code>.json`. Model providers/IDs
are edited in `language_packs/ner_languages.json`. After an update, run
`python tools/validate_packs.py && pytest -q`, then the native-speaker regression suite
before distributing the new pack.

---

# v5.0 additions

The v4.1 contract above is unchanged. `/v1/command` still takes the same multipart fields
and returns the same body, and the action strings are the same. The only difference is
that it now requires the `x-api-key` header like every other protected endpoint. **New
integrations should use `/v1/conversation`/`/v1/conversation/voice` below, not
`/v1/command`** — see `API_INTEGRATION.md`'s legacy-endpoint note.

## E. Patient identity & new endpoints for the app developer

### G. Text request

**Text turn** (use this to test without audio):

```http
POST /v1/conversation
x-api-key: <server key>
{"user_id": "elder-1", "session_id": "abc123", "message": "What is my daughter's name?", "language": "eng"}
```

### H. Voice request

**Voice turn:**

```http
POST /v1/conversation/voice
x-api-key: <server key>
multipart: audio_wav=<file>, user_id=elder-1, session_id=abc123, language=eng, speak=true
```

Response fields the UI needs:

| Field | Use |
|---|---|
| `response_text` | Show as text (accessibility and debugging) |
| `audio_id` / `audio_url` | Fetch and play. `GET /v1/audio/{id}` with the API key. |
| `audio_available` | If `false`, show text only |
| `audio_unavailable_reason` | `NO_TTS_PROVIDER_SUPPORTS_LANGUAGE` means this language has no voice — say so in the UI |
| `kind` | `COMMAND`, `CONVERSATION`, `MEMORY`, `CONFIRMATION`, `REFUSAL`, `FALLBACK`, `ERROR` |
| `action` / `action_accepted` | Execute **only** when `action_accepted` is true, and only the allow-listed strings |
| `requires_confirmation` | Show a large Yes / No. Send the answer as the next turn in the same session. |
| `language` | Which language the answer is in |

**Opening the app — welcome turn:**

```http
POST /v1/conversation/welcome
x-api-key: <server key>
{"user_id": "elder-1", "session_id": null, "language": "eng", "speak": false}
```

Call this once when the app opens, before the user has said anything. It creates a session
(or restores the one you pass as `session_id`) and returns a deterministic greeting —
never LLM-generated, never counted as a user turn, never written into conversation
history, so the user's actual first spoken/typed message afterward is unaffected. Set
`speak: true` to also get a `job_id`/audio the same way the voice endpoint does (poll and
fetch exactly as below); omit it or leave it `false` for text-only. `session_restored` in
the response tells you whether the `session_id` you sent was recognized (`true`) or a new
session was started (`false`).

**Optional: exactly-once delivery.** Send an `Idempotency-Key` header (any string, ≤128
chars) on `POST /v1/conversation` or `POST /v1/conversation/voice` if your client might
retry a request after a timeout. The same key with the same request body returns the
original result without re-running anything (safe against double-executing a confirmed
action like a phone call); the same key with a different body is rejected with `409`. Omit
the header entirely and nothing changes from the behavior above.

**A disabled patient** — set `"active": false` in a `POST /v1/memory/sync` call for that
`user_id` (no separate admin endpoint) — returns `403` on every patient-scoped endpoint
afterward, the same shape as an unauthorized `user_id`. Re-enable with `"active": true`.
Verified live: `tests/integration/test_patient_lifecycle.py`.

## M. Action execution boundary — rules that have not changed

1. Execute only the allow-listed action strings, only when `action_accepted` is true.
2. Never execute transcript text, model text, a URL or a shell command.
3. Keep medicine editing out of the voice path entirely.
4. Send `session_id` back on every turn so context and confirmations work.
5. The microphone is only ever opened after an explicit tap. There is no passive listening.

## Voice UX states

`IDLE → LISTENING → PROCESSING → SPEAKING → ERROR` (`smriti_voice.schemas.VoiceState`).
Each state needs an obvious visual: a large microphone indicator when listening, simple
motion when processing, a speaker indicator when speaking.

## L. Confirmation flow

```
turn 1  "How do I call Bina?"  → requires_confirmation=true, action=NO_ACTION
                                  show Yes / No
turn 2  "Yes"                  → action=CALL_PRIMARY_CONTACT, action_accepted=true
```

Anything other than a clear yes leaves the action pending. Never auto-confirm on a timeout.

## I. TTS polling — voice job lifecycle

States: `queued` → `processing` → one of `completed` / `failed` / `cancelled`. There is no
separate `expired` job state — a job that completed keeps reporting `completed` (that is
historically true: synthesis really did finish), but `GET /v1/voice/jobs/{job_id}` also
returns `audio_expired: true` once the generated file has aged out of the ~15-minute
retention window, so a client can tell "never existed" apart from "existed, now gone"
without a failed audio fetch.

```
POST /v1/conversation/voice                → job_id, job_status=QUEUED
GET  /v1/voice/jobs/{job_id}                → poll until status is terminal
POST /v1/voice/jobs/{job_id}/cancel         → cancel a queued/processing job
GET  /v1/audio/{audio_id}                   → fetch once, promptly
```

- **Queue overload**: if the single TTS worker's queue is full, the job is created but
  immediately marked `failed` with `error_code=QUEUE_OVERLOADED` — poll once and back off
  before retrying, rather than assuming a transient queued state.
- **Processing deadline**: a job stuck `processing` for longer than
  `SMRITI_VOICE_JOB_PROCESSING_DEADLINE_S` (default 180s) is reported (and durably marked)
  `failed` with `error_code=PROCESSING_TIMEOUT` the next time anything reads it — never left
  polling forever.
- **Cancellation**: `POST /v1/voice/jobs/{job_id}/cancel` cancels a still-`queued` or
  `-processing` job; cancelling an already-terminal job is not an error, it just returns
  `cancelled: false` with that job's real current status. If the worker happens to finish
  synthesis at almost the same moment, whichever write reaches the database first wins —
  a job can never report `completed` after being told `cancelled`, and cancelling something
  already completed never undoes it.
- **Restart recovery**: a job left `queued`/`processing` when the process restarts is marked
  `failed` with `error_code=INTERRUPTED_BY_RESTART` at the next startup — never silently
  retried, never left stuck.
- **Ownership**: a job or audio id that doesn't belong to your credential's patient(s) always
  returns **404**, identical to a nonexistent id — you cannot use this to probe whether
  another patient's job exists.
- **Idempotency**: send `Idempotency-Key` on `POST /v1/conversation/voice` if your client
  might retry after a timeout. The same key with the exact same audio bytes +
  user_id/session_id/language/speak returns the original result without re-running
  ASR/conversation/TTS-job-creation; the same key with different content returns **409**.
  Concurrent duplicate submissions with the same key never create two jobs.

## K. Audio retrieval — WAV upload format

What `POST /v1/conversation/voice` and `POST /v1/command` actually enforce today, exactly —
not what might be ideal:

| Property | Enforcement |
|---|---|
| Container | Must be RIFF/WAVE (`RIFF....WAVE` header) |
| `Content-Type` | One of `audio/wav`, `audio/x-wav`, `audio/wave`, `application/octet-stream` |
| Minimum size | 44 bytes, and a `data` chunk must be present |
| Maximum size | `SMRITI_MAX_UPLOAD_BYTES`, default 10 MiB |
| Maximum duration | `SMRITI_MAX_WAV_DURATION_S`, default 60s — **best-effort**: computed from the parsed header where possible, and never rejects a file this check can't parse (that file is left to the container checks above and ultimately the ASR provider) |
| Sample rate / channels / bit depth | **Not enforced by this API.** Whatever the ASR provider accepts or rejects on its own. Do not assume a specific rate is required; a real device recording at 16 kHz mono 16-bit PCM is the safest choice but is a provider expectation, not an API-level requirement today |

Rejections: empty payload → **400**; not a valid/parseable WAV, or wrong content-type →
**415**; over the byte or duration limit → **413**.

## Language capability matrix (verified against this repository's actual code, not assumed)

(See also `LANGUAGE_SUPPORT.md` for the generated, always-current version of this table.)

| Product code | Internal code | ASR | LLM conversation | Deterministic fallback | TTS | Real validation evidence |
|---|---|---|---|---|---|---|
| `hi` | `hin` | Sarvam (online) + local (`validated_local` pack status) | reported `yes` | yes (eng/hin/asm/ben only) | Sarvam | none recorded in `config/language_validation.json` |
| `as` | `asm` | Sarvam (online); local downgraded to `benchmark_only` (placeholder HF model, no real weights) | reported `yes` | yes | **no configured provider** | none recorded |
| `mni` | `mni` | Sarvam (online) + local (`validated_local`) | reported `no`; **not actually blocked** — see note below | **no** — no deterministic template for this language | **no configured provider** | none recorded |
| `kha` | `kha` | Local NE-ASR only, `benchmark_only` (no cloud ASR at all) | reported `no`; **not actually blocked** | **no** | **no configured provider** | none recorded |
| `lus` | `lus` | Local NE-ASR only, `benchmark_only` | reported `no`; **not actually blocked** | **no** | **no configured provider** | none recorded |
| `en` | `eng` | Sarvam (online); local `benchmark_only` (uses generic Whisper, not IndicConformer) | reported `yes` | yes | Sarvam | none recorded |

**Read `GET /v1/languages` live, always** — this table is a snapshot for planning purposes,
not something to hard-code. `config/language_validation.json`'s validated-language list is
currently empty, so every language reports at most `BENCHMARK_ONLY`/`NOT_YET_TESTED`, never
`SUPPORTED`, regardless of what this table says an adapter *could* do — adapter existence is
never treated as proof it works.

**"LLM conversation: reported `no`" for `mni`/`kha`/`lus` needs a precise reading — verified,
not assumed, against `conversation/manager.py`/`llm/router.py`: the capability matrix's
`LLM_LANGUAGES` allowlist (`language/capabilities.py`) is consumed *only* by the
`/v1/languages` reporting endpoint. Nothing in the actual conversation pipeline checks it —
`ConversationManager._converse()` calls the configured LLM unconditionally, and the system
prompt correctly names all three languages (`conversation/prompts.py`'s `LANGUAGE_NAMES`
includes Manipuri/Khasi/Mizo). A real conversation turn in any of these three languages is
**not technically blocked**; it reaches Groq/Qwen exactly like any other language and the
model attempts to answer in it (verified:
`test_llm_conversation_is_not_actually_blocked_for_unlisted_languages`). What's genuinely
true is narrower and more honest than "no path exists": **nobody has measured whether that
output is any good** for these three languages, so the capability matrix correctly declines
to claim support — but if your product plan needs to know "can a user actually try talking
to it in Khasi today," the answer is yes, with entirely unvalidated response quality, not
"the system will refuse." Decide deliberately whether to (a) leave this as unvalidated/
best-effort, (b) invest in measuring and validating quality for these languages, or (c) add
an explicit block if unvalidated attempts are undesirable for your product — none of the
three is implemented today; this is a decision, not a bug.

## J. Cancellation

`POST /v1/voice/jobs/{job_id}/cancel` — see "I. TTS polling" above for full semantics
(race-safety, ownership, what happens if the job already completed). Verified live.

## N. Idempotency

`Idempotency-Key` header (≤128 chars) on `POST /v1/conversation`/`/v1/conversation/voice`
— same key + same body returns the original result without re-running anything; same key +
different body → `409`. See "I. TTS polling" above for the voice-specific detail
(concurrent duplicate submissions never create two jobs).

## O. Error handling

`400` malformed/empty input, `401` missing/invalid key, `403` cross-patient/unauthorized/
disabled, `404` unknown job/audio (identical shape to "not yours" — no probing signal),
`409` idempotency conflict, `413` over size/duration limit, `415` not a valid/parseable
WAV, `429` rate limited, `503` server not configured. Every error body is
`{"error": "CODE", "detail": "..."}` — never a raw exception string. See "K. Audio
retrieval" above for the WAV-specific rejection codes.

## P. Security rules — backend developer notes

- Set `SMRITI_API_KEY`. Without it, protected endpoints return **503** by design.
- Never set `SMRITI_ALLOW_UNAUTHENTICATED=1` on anything network-facing.
- Provider keys (Groq/Sarvam/HF) live on the server only — never in the APK or in browser
  JavaScript, and never forwarded in any VoiceBot response.
- The VoiceBot `x-api-key` credential itself must never reach Flutter or a browser either —
  Backend calls VoiceBot server-to-server only.
- A caller-supplied `user_id` is never trusted without authorization — it must match (or
  be a member of) the identities bound to the presented key, or the request is `403`.
- Job/audio ownership is enforced the same way everywhere: a wrong-patient id is `404`,
  identical to a nonexistent one.
- Transcript/model output is never directly executable — only the allow-listed action
  strings, only when `action_accepted` is true (see **M** above).
- `action_accepted: true` is not proof a side effect actually happened — no real calling or
  reminder-scheduling executor exists in this repository today.
- Logs never contain secrets, raw audio, full transcripts, or full memory payloads —
  `tests/unit/test_privacy_data_minimization.py`.
- `GET /v1/health` reports capabilities and credential presence as booleans, never values.
- `GET /v1/languages` returns the capability matrix; use it to decide whether to offer
  voice output for a given language — and note `validated` is a separate, currently-false-
  for-everything field, not implied by a provider being configured.

## Q. Final E2E checklist

Run against a real deployment (not mocks) before calling integration complete:

- [ ] Real patient UUID → `POST /v1/memory/sync` (provisions) → authorized text turn succeeds
- [ ] Unknown/unauthorized UUID → `403`
- [ ] Disabled patient (`active:false`) → `403` on every subsequent call
- [ ] Memory round-trip: sync a person → ask about them → correct answer
- [ ] Stale revision → rejected; conflicting same-revision → rejected; identical replay → no-op
- [ ] Full voice flow: record → Backend → VoiceBot → job → poll → audio → playback
- [ ] Cancellation mid-job leaves no audio behind
- [ ] Network loss during polling → Flutter recovers/resumes without duplicating the request
- [ ] VoiceBot credential never appears in any Flutter-visible response
- [ ] Physical device, real human speech (not synthetic) — **not yet run; requires a real
      device and a working Backend + Flutter**
- The database is SQLite at `SMRITI_DB_PATH`. The caregiver app is the source of truth for
  family, medicines, appointments and routine — write those rows with `source='caregiver'`.
