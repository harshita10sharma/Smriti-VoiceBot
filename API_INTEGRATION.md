# SMRITI VoiceBot API — backend integration guide

This document is for the backend/mobile-app developer integrating with the
deployed SMRITI VoiceBot service. It covers the two endpoints you need: a
voice turn (audio in, audio out) and a text turn (no audio). Both go through
the same VoiceBot: safety layer, deterministic commands, conversation, memory
and TTS.

## Base URL

```
https://<your-tailnet-hostname>.ts.net
```

This deployment runs on a Windows PC and is exposed to the public internet
via [Tailscale Funnel](https://tailscale.com/kb/1223/funnel), which provides
the HTTPS certificate and public hostname — there is no separate cloud
service to name. Ask the operator for the exact hostname currently in use
(it looks like `desktop-xxxxxxx.tailnetname.ts.net`); it stays stable as
long as the same device and Tailscale account run the tunnel.
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

**Identity binding.** Each API key is bound server-side to exactly one
`user_id`. Whatever `user_id` you send in a request **must match the identity
your key is bound to**, or the request fails with `403` — you cannot use one
key to read or act on another user's data by changing `user_id` in the JSON
body. If you have been issued one key per elderly user (the deployment's
"multi-user mode"), send that user's own key on every request for them. If
you have been issued a single shared key (the deployment's "single-user
mode"), it is bound to one fixed `user_id` and can only ever act as that one
user — ask the operator which mode your deployment uses.

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

`status` is one of `queued`, `processing`, `completed`, `failed`. Poll every
1–2 seconds until it is no longer `queued`/`processing`. On `completed`,
`audio_id`/`audio_url` are set — fetch them the same way as before. On
`failed`, `error_code` explains why (e.g. `NO_TTS_PROVIDER_SUPPORTS_LANGUAGE`
if nothing covers that language); `response_text` from the original POST is
still the correct answer to show — text never depends on TTS succeeding.

A job belonging to a different `user_id` than the one your key is bound to
returns `404`, the same as a job id that never existed — job ids cannot be
used to probe for other users' jobs.

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

## Retrieving audio

`audio_id` is an opaque, short-lived reference — not a file path. Fetch the
actual WAV once:

```
GET /v1/audio/{audio_id}
x-api-key: <your VoiceBot API key>
```

Returns `audio/wav` bytes directly, or `404` if the id is invalid, unknown,
or has expired (default retention: 15 minutes; generate-then-fetch promptly).
There is no way to list, enumerate or browse audio — you must already hold
the exact `audio_id` from a prior response.

## Supported languages

```
GET /v1/languages          (no auth required)
GET /v1/languages/{code}
```

Returns the full capability matrix (which languages have ASR/TTS/LLM
support, and whether that support is validated). The four languages with
**real, validated, end-to-end Indic Parler-TTS voice output** are:

| Code | Language |
|---|---|
| `asm` | Assamese |
| `brx` | Bodo |
| `mni` | Manipuri |
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
| `403` | `user_id` does not match the authenticated identity, or a session belongs to another user |
| `404` | Audio id not found/expired, unknown/not-yours job id, or unknown language code on `/v1/languages/{code}` |
| `413` | Uploaded WAV exceeds the server's upload limit |
| `415` | Not a valid/parseable WAV file |
| `422` | Request failed schema validation (e.g. malformed `user_id`) |
| `429` | Rate limit exceeded |
| `503` | The service is not configured to authenticate requests, or the bound user identity is not configured — this is a deployment misconfiguration, not a client error |
| `500` | Internal error (VoiceBot/TTS/ASR processing failure); no stack trace or secret is ever included in the body |

Typed VoiceBot errors are returned as `{"error": "<CODE>", "detail": "..."}`
with HTTP `400`.

## curl example

```bash
curl -X POST https://<your-tailnet-hostname>.ts.net/v1/conversation/voice \
  -H "x-api-key: $VOICEBOT_API_KEY" \
  -F "audio_wav=@utterance.wav" \
  -F "user_id=elder-1" \
  -F "language=asm"
# -> { "response_text": "...", "job_id": "50f5...", "job_status": "QUEUED", ... }

# Poll until the job is no longer queued/processing:
curl https://<your-tailnet-hostname>.ts.net/v1/voice/jobs/50f5dc5393764e6b8ade7769e28aca72 \
  -H "x-api-key: $VOICEBOT_API_KEY"
# -> { "status": "completed", "audio_id": "122a...", "audio_url": "/v1/audio/122a...", ... }

# Then fetch the audio:
curl https://<your-tailnet-hostname>.ts.net/v1/audio/<audio_id> \
  -H "x-api-key: $VOICEBOT_API_KEY" \
  -o reply.wav
```

## Python example

```python
import time
import requests

BASE_URL = "https://<your-tailnet-hostname>.ts.net"
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
const BASE_URL = "https://<your-tailnet-hostname>.ts.net";
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
