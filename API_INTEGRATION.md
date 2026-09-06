# SMRITI VoiceBot API — backend integration guide

This document is for the backend/mobile-app developer integrating with the
deployed SMRITI VoiceBot service. It covers the two endpoints you need: a
voice turn (audio in, audio out) and a text turn (no audio). Both go through
the same VoiceBot: safety layer, deterministic commands, conversation, memory
and TTS.

## Base URL

```
https://<service-name>.onrender.com
```

Replace `<service-name>` with the actual Render service name once deployed.
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

### Response — `200 OK`

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
  "audio_id": "e13a1260e49d035e94c0d9d785489f76",
  "audio_url": "/v1/audio/e13a1260e49d035e94c0d9d785489f76",
  "audio_available": true,
  "audio_unavailable_reason": null,
  "tts_provider": "indic_parler",
  "requires_confirmation": false,
  "metadata": { "total_latency_ms": 21870, "tts_provider": "indic_parler", "...": "..." }
}
```

`language` is always the language the VoiceBot actually detected and
answered in — the same code flows through detection → response → TTS with
**no silent substitution**. If TTS could not produce audio in that language,
`audio_available` is `false` and `audio_unavailable_reason` explains why
(e.g. `NO_TTS_PROVIDER_SUPPORTS_LANGUAGE`); `response_text` is still present,
so the app can always fall back to on-screen text.

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
| `404` | Audio id not found/expired, or unknown language code on `/v1/languages/{code}` |
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
curl -X POST https://<service-name>.onrender.com/v1/conversation/voice \
  -H "x-api-key: $VOICEBOT_API_KEY" \
  -F "audio_wav=@utterance.wav" \
  -F "user_id=elder-1" \
  -F "language=asm"

# Then fetch the audio:
curl https://<service-name>.onrender.com/v1/audio/<audio_id> \
  -H "x-api-key: $VOICEBOT_API_KEY" \
  -o reply.wav
```

## Python example

```python
import requests

BASE_URL = "https://<service-name>.onrender.com"
API_KEY = "..."  # from your secret store, never hard-coded

with open("utterance.wav", "rb") as f:
    response = requests.post(
        f"{BASE_URL}/v1/conversation/voice",
        headers={"x-api-key": API_KEY},
        data={"user_id": "elder-1", "language": "asm"},
        files={"audio_wav": ("utterance.wav", f, "audio/wav")},
        timeout=60,  # Indic Parler-TTS synthesis on CPU can take ~20-30s
    )
response.raise_for_status()
turn = response.json()
print(turn["response_text"], turn["language"])

if turn["audio_available"]:
    audio = requests.get(
        f"{BASE_URL}{turn['audio_url']}",
        headers={"x-api-key": API_KEY},
        timeout=30,
    )
    with open("reply.wav", "wb") as out:
        out.write(audio.content)
```

## JavaScript / TypeScript example

```ts
const BASE_URL = "https://<service-name>.onrender.com";
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
  const turn = await res.json();

  let audioBlob: Blob | null = null;
  if (turn.audio_available) {
    const audioRes = await fetch(`${BASE_URL}${turn.audio_url}`, {
      headers: { "x-api-key": API_KEY },
    });
    audioBlob = await audioRes.blob();
  }
  return { turn, audioBlob };
}
```

**Important:** `x-api-key` is a server-side secret. Do not embed it in a
mobile app binary or browser JavaScript that ships to end users — call this
API from your own backend, which holds the key, and let your backend proxy
requests from the app.
