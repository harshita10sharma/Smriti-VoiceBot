# `POST /v1/conversation/voice` — multipart example

Not representable as plain JSON (it's `multipart/form-data`). Real `curl` example:

```bash
curl -X POST https://your-deployment/v1/conversation/voice \
  -H "x-api-key: $VOICEBOT_API_KEY" \
  -H "Idempotency-Key: optional-client-chosen-string" \
  -F "audio_wav=@utterance.wav;type=audio/wav" \
  -F "user_id=elder-1" \
  -F "session_id=8ed26ef3e630462f8443a0268819d000" \
  -F "language=eng" \
  -F "speak=true"
```

Field notes (verified against `schemas.py`/`api/routes/voice.py`, see `INTEGRATION_CONTRACT.md` §2/§3):

| Field | Required | Notes |
|---|---|---|
| `audio_wav` | **yes** | RIFF/WAVE, `audio/wav`\|`audio/x-wav`\|`audio/wave`\|`application/octet-stream`, ≤10 MiB, ≤60s (best-effort) |
| `user_id` | **yes** | Must be authorized for the calling API key |
| `session_id` | no | Omit to start a new session; send a prior `session_id` to continue one |
| `language` | no | Omit to let the server detect it from the transcript |
| `speak` | no | Default `true`; set `false` for text-only (no TTS job created) |

Response shape: see `conversation_response.json` in this directory plus the voice-specific
additions — `transcript`, `job_id`, `job_status`, `audio_id` (always `null` here), `audio_url`
(always `null` here), `audio_available` (always `false` here), `audio_unavailable_reason`,
`tts_provider` (always `null` here). TTS is asynchronous — poll `job_queued.json` /
`job_processing.json` / `job_completed.json` / `job_failed.json` in this directory for what
each terminal/non-terminal poll looks like.
