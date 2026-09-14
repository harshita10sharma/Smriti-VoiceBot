# SMRITI VoiceBot API — backend integration guide

> This is the endpoint-by-endpoint request/response reference, audited against the actual
> FastAPI schemas in `smriti_voice/api/routes/`. For a narrative walkthrough (architecture,
> ownership, implementation sequence), start at `docs/VOICEBOT_INTEGRATION_GUIDE.md`
> instead — the two are complementary, not duplicates.

This document is for the backend/mobile-app developer integrating with the
deployed SMRITI VoiceBot service. It covers the two endpoints you need: a
voice turn (audio in, audio out) and a text turn (no audio). Both go through
the same VoiceBot: safety layer, deterministic commands, conversation, memory
and TTS.

## Base URL

```
https://15-206-144-216.nip.io
```

Current live deployment: AWS EC2 (`ap-south-1`, `m7i-flex.large`), Docker, Caddy
terminating HTTPS with a real Let's Encrypt certificate. `nip.io` is a free wildcard-DNS
service that resolves that hostname to the deployment's Elastic IP — see
`docs/AWS_DEPLOYMENT.md` for the full architecture.
For local development this is `http://127.0.0.1:8000`.

## Authentication

Every endpoint except `GET /v1/health` and `GET /v1/languages` requires an
API key on every request:

```
x-api-key: <your VoiceBot API key>
```

- Missing or wrong key → `401 Invalid API key`.
- Server has no key configured at all → `503 API authentication is not
  configured` (fails closed; it never silently allows unauthenticated access).
- Rate limited → `429 Too many requests`.

**The API key is server-side only.** Your backend stores it as a secret and
calls this API from your server — never embed it in a mobile app binary or
in browser JavaScript that ships to end users.

**Identity binding.** Each API key is bound server-side to either one `user_id`
or an explicit list of authorized `user_id`s. Whatever `user_id` you send in a
request **must match (or be a member of) the identities your key is bound
to**, or the request fails with `403` — you cannot access another user's data
by changing `user_id` in the JSON body, ever. Three deployment shapes exist,
and the operator will tell you which one you've been given:
- **Single-user**: one key, bound to exactly one elder.
- **One key per patient**: you're issued a separate key per elderly user;
  send that user's own key for their requests.
- **Backend allow-list**: one key authorized for several named patients at
  once (e.g. `["elder-1", "elder-2"]`); send the same key for any of them,
  with the correct `user_id` in the request — a `user_id` not on that key's
  list still returns `403`.

This key is generated with:

```bash
python -m smriti_voice.api.generate_key
```

It is **not** the same thing as `HF_TOKEN`. `HF_TOKEN` is a server-side-only
credential the VoiceBot uses to download the gated Indic Parler-TTS model
from Hugging Face; it is never read from a request, never returned in a
response, and the backend developer never needs it.

## Endpoint: voice turn (audio in, audio out)

```
POST /v1/conversation/voice
Content-Type: multipart/form-data
x-api-key: <your VoiceBot API key>
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `audio_wav` | file | yes | A WAV file, one utterance. Must start with a valid `RIFF`/`WAVE` header. |
| `user_id` | string | yes | Must match the identity bound to the API key on the server (`SMRITI_AUTH_USER_ID`); otherwise `403`. |
| `session_id` | string | no | Send back the `session_id` from the previous response to continue a conversation (multi-turn context, pending confirmations). |
| `language` | string | no | A 3-letter code, e.g. `asm`, `brx`, `mni`, `npi`, `eng`, `hin`. If omitted, the server detects it from the audio. If given and not a known SMRITI language, the request fails with `400 Unknown language`. |
| `speak` | bool | no | Default `true`. Set `false` to skip TTS and only get text back (faster). |

### Response — `200 OK` (returns in a few seconds — audio is not ready yet)

Speech-to-text and the conversation answer are computed synchronously, but
**text-to-speech is asynchronous**: Indic Parler-TTS can take well over a
minute on CPU, longer than most HTTP proxies (including Cloudflare Tunnel)
will wait for a single request. So this endpoint returns as soon as the
text answer exists, carrying a `job_id` you poll for the audio:

```json
{
  "request_id": "a1b2c3d4e5f6...",
  "session_id": "abc123",
  "response_text": "আপুনি কেনে আছে?",
  "language": "asm",
  "language_confidence": 0.94,
  "kind": "CONVERSATION",
  "action": "NO_ACTION",
  "action_accepted": false,
  "transcript": "মই ভাল আছোঁ",
  "job_id": "50f5dc5393764e6b8ade7769e28aca72",
  "job_status": "QUEUED",
  "audio_id": null,
  "audio_url": null,
  "audio_available": false,
  "audio_unavailable_reason": "TTS_PROCESSING",
  "tts_provider": null,
  "requires_confirmation": false,
  "metadata": { "total_latency_ms": 4200, "...": "..." }
}
```

`language` is always the language the VoiceBot actually detected and
answered in — the same code flows through detection → response → TTS with
**no silent substitution**. Show `response_text` to the user immediately;
it does not wait on TTS. `job_id` is `null` when `speak=false` was sent, or
when an error (e.g. no speech detected) meant TTS was never attempted —
check `job_status == "NOT_REQUESTED"` for that case.

## Polling for the generated audio

```
GET /v1/voice/jobs/{job_id}
x-api-key: <your VoiceBot API key>
```

```json
{
  "job_id": "50f5dc5393764e6b8ade7769e28aca72",
  "status": "completed",
  "language": "brx",
  "audio_id": "122a4bd2cfd5985c9af58e49314c1f83",
  "audio_url": "/v1/audio/122a4bd2cfd5985c9af58e49314c1f83",
  "tts_provider": "indic_parler",
  "error_code": null
}
```

`status` is one of `queued`, `processing`, `completed`, `failed`, `cancelled`. Poll every
1–2 seconds until it is no longer `queued`/`processing`. On `completed`,
`audio_id`/`audio_url` are set — fetch them the same way as before. On
`failed`, `error_code` explains why (e.g. `NO_TTS_PROVIDER_SUPPORTS_LANGUAGE`
if nothing covers that language); `response_text` from the original POST is
still the correct answer to show — text never depends on TTS succeeding.

A job belonging to a different `user_id` than the one your key is bound to
returns `404`, the same as a job id that never existed — job ids cannot be
used to probe for other users' jobs.

## Cancelling a voice job

```
POST /v1/voice/jobs/{job_id}/cancel
x-api-key: <your VoiceBot API key>
```

```json
{"job_id": "50f5dc5393764e6b8ade7769e28aca72", "status": "cancelled", "cancelled": true}
```

Race-safe against the synthesis worker: cancelling a job that has already completed leaves
the completed result untouched (`cancelled: false` in the response); cancelling a
queued/processing job stops it and any later poll reports `status: "cancelled"`,
`error_code: "CANCELLED_BY_CLIENT"`, with no audio ever produced for it. Verified live
against the deployment above.

## Endpoint: text turn (no audio in)

Useful when your app already has its own speech-to-text, or for a text chat
UI:

```
POST /v1/conversation
Content-Type: application/json
x-api-key: <your VoiceBot API key>

{"user_id": "elder-1", "session_id": "abc123", "message": "How are you?", "language": "asm"}
```

Returns the same shape as above minus `transcript`/`audio_*`/`tts_provider`
(those are voice-turn-only fields). To get audio for a text turn's answer,
call the voice endpoint, or use `/v1/conversation` for text-only UI and treat
audio as an separate, optional step your app doesn't need.

## Endpoint: welcome (session-opening greeting)

```
POST /v1/conversation/welcome
Content-Type: application/json
x-api-key: <your VoiceBot API key>

{"user_id": "elder-1", "language": "asm"}
```

Returns a greeting (`kind: "WELCOME"`) and a fresh `session_id` — use this when the app
first opens or resumes, before the user has said anything. `session_restored` reports
whether an existing session was found idle-timeout-eligible and reused.

## Legacy endpoint: `/v1/command`

```
POST /v1/command
```

This is the original v4.1 endpoint, kept only for backward compatibility with existing
callers. It takes `audio_wav`/`language`/`request_id?` and returns the v4.1 response shape
— it does not have memory sync, voice jobs, or the v5 conversational routing.
**New integrations should use `/v1/conversation` and `/v1/conversation/voice` above, not
this endpoint.** It remains API-key-only (no `SMRITI_AUTH_USER_ID` binding) because it does
not access personal data.

## `GET /v1/tools`

Returns the registered tool schemas (`{"count": N, "advertised_to_model": [...]}`) —
introspection only, useful for debugging what the model can propose. Not needed for a
normal Backend integration.

## Retrieving audio

`audio_id` is an opaque, short-lived reference — not a file path. Fetch the
actual WAV once:

```
GET /v1/audio/{audio_id}
x-api-key: <your VoiceBot API key>
```

Returns `audio/wav` bytes directly, or `404` if the id is invalid, unknown,
has expired (default retention: 15 minutes; generate-then-fetch promptly), or
belongs to a `user_id` your key isn't authorized for — ownership is enforced
the same way as everywhere else, so one patient's audio is never served
against another patient's identity. There is no way to list, enumerate or
browse audio — you must already hold the exact `audio_id` from a prior
response.

## Endpoint: caregiver memory sync

Your backend's own database is the source of truth for a patient's family,
medicines and daily routine. Whenever that data changes, push the **complete
current set** for that patient:

```
POST /v1/memory/sync
Content-Type: application/json
x-api-key: <your VoiceBot API key>

{
  "user_id": "elder-1",
  "display_name": "Test Elder",
  "timezone": "Asia/Kolkata",
  "language_code": "eng",
  "active": true,
  "source_revision": 12,
  "schema_version": 1,
  "family_members": [
    {"external_id": "p1", "name": "Bina", "relationship": "daughter",
     "memory_prompt": "lives in Guwahati", "is_deceased": false, "phone_available": true}
  ],
  "medicines": [
    {"external_id": "m1", "name": "Metformin", "dose": "500mg", "schedule": "morning, after food",
     "chosen_time_min": 480, "window_start_min": 450, "window_end_min": 510,
     "days_of_week": "1,2,3,4,5,6,7", "active": true}
  ],
  "daily_routines": [
    {"external_id": "r1", "time": "08:00", "activity": "breakfast"}
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_id` | string | yes | Must be authorized for your key (see Identity binding above), otherwise `403`. Accepts your Supabase patient UUID directly. |
| `display_name` | string | no | Patient's display name. |
| `timezone` | string | no | IANA timezone (e.g. `"Asia/Kolkata"`) — affects "tonight"/"tomorrow" medicine-time resolution. |
| `language_code` | string | no | Patient's default language, internal 3-letter code. |
| `active` | bool | no, default `true` | Set `false` to disable/revoke a patient — no separate admin endpoint. |
| `source_revision` | int | no | If set, enables staleness/conflict protection (see below). Omit for always-apply. |
| `schema_version` | int | no | Caregiver-schema version the payload was produced against. |
| `family_members[].external_id` | string | no | Stable ID from your own database, for correlation across syncs. |
| `family_members[].name` | string | yes | 1–80 characters. |
| `family_members[].relationship` | string | yes | 1–40 characters, e.g. `"daughter"`. |
| `family_members[].memory_prompt` | string | no | Free-text context the assistant may reference, e.g. `"lives in Guwahati"`. |
| `family_members[].is_deceased` | bool | no, default `false` | Passed through as context; never inferred from absence. |
| `family_members[].phone_available` | bool | no, default `false` | **Never send an actual phone number** — see below. Does not by itself enable calling. |
| `medicines[].external_id` | string | no | Stable ID for correlation across syncs. |
| `medicines[].name` | string | yes | 1–80 characters. |
| `medicines[].dose` | string | no | Free text, e.g. `"500mg"`. |
| `medicines[].schedule` | string | no | Free text, e.g. `"morning, after food"`. |
| `medicines[].chosen_time_min` / `window_start_min` / `window_end_min` | int | no | Minutes from midnight, 0–1439. Structured, non-wrapping window (`start <= chosen <= end`) used for deterministic "is it time for X" answers. |
| `medicines[].days_of_week` | string | no | Comma-separated ISO weekdays, Monday=`1`..Sunday=`7`, e.g. `"1,2,3,4,5,6,7"`. |
| `medicines[].active` | bool | no, default `true` | |
| `daily_routines[].external_id` | string | no | Stable ID for correlation across syncs. |
| `daily_routines[].time` | string | no | 24-hour `HH:MM`, e.g. `"08:00"`. Anything else is rejected with `422`. |
| `daily_routines[].activity` | string | yes | 1–120 characters. |

### Revision handling (when `source_revision` is set)

- A revision **older** than the currently applied one is rejected (`400`, current revision
  named in the error).
- The **same** revision with **different** content is rejected as a conflict (`400`) — use a
  new, higher revision instead.
- The **same** revision with **identical** content is a harmless no-op (`status: "no_op"` in
  the response).
- All three behaviors verified live against the deployment above.

Any field not listed above — including `phone`, `phone_number`, `mobile`,
`contact_number`, `telephone`, or any other raw contact value — is **rejected
with `422`**, not silently dropped. This endpoint only ever accepts
`phone_available: true/false`; it never stores an actual number, so a family
member created exclusively through this endpoint cannot be called by the
voice assistant until a real number is provisioned through whatever other,
more privileged channel your deployment uses for that (out of scope for this
API).

**Full replacement, per call, per category.** Each array you send *replaces*
that patient's entire caregiver-synced dataset for that category — not a
merge. Sending `"family_members": []` clears all caregiver-synced family
members for that patient. Data your own backend didn't create (the patient's
own words, the assistant's notes, seeded demo data) is never touched, no
matter what you send. Calling this endpoint twice with identical data is a
no-op — it does not create duplicates. The whole request is atomic: if
anything fails, nothing for that patient changes.

### Response — `200 OK`

```json
{
  "success": true,
  "user_id": "elder-1",
  "family_members_synced": 1,
  "medicines_synced": 1,
  "daily_routines_synced": 1,
  "status": "applied",
  "source_revision": 12
}
```
`status` is `"applied"` or `"no_op"` (identical-revision replay).

## Supported languages

```
GET /v1/languages          (no auth required)
GET /v1/languages/{code}
```

Returns the full capability matrix. Each language separately reports ASR/LLM/TTS/
deterministic-fallback **capability** (whether a configured provider can attempt it) and a
`validated` flag — these are not the same claim. Provider execution (a real API call
succeeding) is not native-speaker quality validation. **No language currently has
`validated: true`** — see `LANGUAGE_SUPPORT.md`. `mni` is Meiteilon/Manipuri and is never
mapped to `mn` (Mongolian).

The four languages with **real Indic Parler-TTS voice output exercised** (model loads,
synthesis succeeds, audio is real and playable — not yet native-speaker validated) are:

| Code | Language |
|---|---|
| `asm` | Assamese |
| `brx` | Bodo |
| `mni` | Meiteilon/Manipuri |
| `npi` | Nepali |

Sending an unrecognized language code returns `400 Unknown language: '<code>'`
— the API never silently falls back to English or Hindi.

## Health check

```
GET /v1/health          (no auth required, never returns a credential)
```

Returns booleans/status only — safe to poll from an uptime monitor.

## Error codes

| Status | Meaning |
|---|---|
| `400` | Malformed request or unknown language |
| `401` | Missing or invalid `x-api-key` |
| `403` | `user_id` is not authorized for the API credential used, or a session belongs to another user |
| `404` | Audio id not found/expired/not-yours, unknown/not-yours job id, or unknown language code on `/v1/languages/{code}` |
| `413` | Uploaded WAV exceeds the server's upload limit |
| `415` | Not a valid/parseable WAV file |
| `422` | Request failed schema validation — malformed `user_id`, an invalid `daily_routines[].time`, a missing required field, or **any unrecognized field** (this is what rejects a raw phone number sent to `/v1/memory/sync`) |
| `429` | Rate limit exceeded |
| `503` | The service is not configured to authenticate requests, or the bound user identity is not configured — this is a deployment misconfiguration, not a client error |
| `500` | Internal error (VoiceBot/TTS/ASR processing failure); no stack trace or secret is ever included in the body |

Typed VoiceBot errors are returned as `{"error": "<CODE>", "detail": "..."}`
with HTTP `400`.

## curl example

```bash
curl -X POST https://15-206-144-216.nip.io/v1/conversation/voice \
  -H "x-api-key: $VOICEBOT_API_KEY" \
  -F "audio_wav=@utterance.wav" \
  -F "user_id=elder-1" \
  -F "language=asm"
# -> { "response_text": "...", "job_id": "50f5...", "job_status": "QUEUED", ... }

# Poll until the job is no longer queued/processing:
curl https://15-206-144-216.nip.io/v1/voice/jobs/50f5dc5393764e6b8ade7769e28aca72 \
  -H "x-api-key: $VOICEBOT_API_KEY"
# -> { "status": "completed", "audio_id": "122a...", "audio_url": "/v1/audio/122a...", ... }

# Then fetch the audio:
curl https://15-206-144-216.nip.io/v1/audio/<audio_id> \
  -H "x-api-key: $VOICEBOT_API_KEY" \
  -o reply.wav
```

## Python example

```python
import time
import requests

BASE_URL = "https://15-206-144-216.nip.io"
API_KEY = "..."  # from your secret store, never hard-coded

with open("utterance.wav", "rb") as f:
    response = requests.post(
        f"{BASE_URL}/v1/conversation/voice",
        headers={"x-api-key": API_KEY},
        data={"user_id": "elder-1", "language": "asm"},
        files={"audio_wav": ("utterance.wav", f, "audio/wav")},
        timeout=30,  # ASR + conversation only; this returns long before TTS finishes
    )
response.raise_for_status()
turn = response.json()
print(turn["response_text"], turn["language"])  # show this immediately

audio_bytes = None
if turn["job_id"]:
    for _ in range(120):  # poll for up to ~2 minutes
        job = requests.get(
            f"{BASE_URL}/v1/voice/jobs/{turn['job_id']}",
            headers={"x-api-key": API_KEY}, timeout=10,
        ).json()
        if job["status"] == "completed":
            audio = requests.get(
                f"{BASE_URL}{job['audio_url']}",
                headers={"x-api-key": API_KEY}, timeout=30,
            )
            audio_bytes = audio.content
            break
        if job["status"] == "failed":
            print("no audio for this reply:", job["error_code"])
            break
        time.sleep(1)

if audio_bytes:
    with open("reply.wav", "wb") as out:
        out.write(audio_bytes)
```

## JavaScript / TypeScript example

```ts
const BASE_URL = "https://15-206-144-216.nip.io";
const API_KEY = process.env.VOICEBOT_API_KEY!; // never bundle this in client-side JS

async function sendUtterance(wavBlob: Blob, userId: string, language?: string) {
  const form = new FormData();
  form.append("audio_wav", wavBlob, "utterance.wav");
  form.append("user_id", userId);
  if (language) form.append("language", language);

  const res = await fetch(`${BASE_URL}/v1/conversation/voice`, {
    method: "POST",
    headers: { "x-api-key": API_KEY },
    body: form,
  });
  if (!res.ok) throw new Error(`VoiceBot request failed: ${res.status}`);
  const turn = await res.json(); // show turn.response_text right away

  let audioBlob: Blob | null = null;
  if (turn.job_id) {
    for (let i = 0; i < 120; i++) {
      const jobRes = await fetch(`${BASE_URL}/v1/voice/jobs/${turn.job_id}`, {
        headers: { "x-api-key": API_KEY },
      });
      const job = await jobRes.json();
      if (job.status === "completed") {
        const audioRes = await fetch(`${BASE_URL}${job.audio_url}`, {
          headers: { "x-api-key": API_KEY },
        });
        audioBlob = await audioRes.blob();
        break;
      }
      if (job.status === "failed") break; // job.error_code explains why
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  return { turn, audioBlob };
}
```

**Important:** `x-api-key` is a server-side secret. Do not embed it in a
mobile app binary or browser JavaScript that ships to end users — call this
API from your own backend, which holds the key, and let your backend proxy
requests from the app.
