# Smriti VoiceBot handoff

## What the app developer needs

1. Bundle the Python service on the device/server OR call the HTTP service.
2. Send `language` explicitly from the caregiver profile. Do not silently guess a language.
3. On the red microphone tap, capture one utterance and send one WAV request.
4. If `accepted=false`, do not execute anything. Show the large touch fallback.
5. Execute only these action strings: `OPEN_PLAY`, `OPEN_MY_PEOPLE`, `OPEN_TODAY`, `CALL_BINA`, `OPEN_MEDICINE`, `HELP`, `STOP`.
6. Never expose an arbitrary transcript as an executable command.
7. Keep medicine editing outside voice commands.

## What the web/backend developer needs

- Optional `SMRITI_API_KEY` for HTTP authentication.
- `GET /v1/health`
- `GET /v1/languages`
- `POST /v1/command` multipart fields: `audio_wav`, `language`, optional `request_id`.

## Offline rule

The production runtime must have `SMRITI_PROVISIONING` unset/0 and must have the model pack already installed.
No cloud request is made by the voice engine. Provisioning is a separate operation performed before deployment.

## Updating later

Language phrases are edited in `language_packs/commands_<code>.json`.
Model providers/IDs are edited in `language_packs/ner_languages.json`.
After an update, run:

```bash
python tools/validate_packs.py
pytest -q
```

Then run the native-speaker regression suite before distributing the new pack.

---

# v5.0 additions

The v4.1 contract above is unchanged. `/v1/command` still takes the same multipart fields
and returns the same body, and the action strings are the same. The only difference is
that it now requires the `x-api-key` header like every other protected endpoint.

## New endpoints for the app developer

**Text turn** (use this to test without audio):

```http
POST /v1/conversation
x-api-key: <server key>
{"user_id": "elder-1", "session_id": "abc123", "message": "What is my daughter's name?", "language": "eng"}
```

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

## Rules that have not changed

1. Execute only the allow-listed action strings, only when `action_accepted` is true.
2. Never execute transcript text, model text, a URL or a shell command.
3. Keep medicine editing out of the voice path entirely.
4. Send `session_id` back on every turn so context and confirmations work.
5. The microphone is only ever opened after an explicit tap. There is no passive listening.

## Voice UX states

`IDLE → LISTENING → PROCESSING → SPEAKING → ERROR` (`smriti_voice.schemas.VoiceState`).
Each state needs an obvious visual: a large microphone indicator when listening, simple
motion when processing, a speaker indicator when speaking.

## Confirmation flow

```
turn 1  "How do I call Bina?"  → requires_confirmation=true, action=NO_ACTION
                                  show Yes / No
turn 2  "Yes"                  → action=CALL_PRIMARY_CONTACT, action_accepted=true
```

Anything other than a clear yes leaves the action pending. Never auto-confirm on a timeout.

## Backend developer notes

- Set `SMRITI_API_KEY`. Without it, protected endpoints return **503** by design.
- Never set `SMRITI_ALLOW_UNAUTHENTICATED=1` on anything network-facing.
- Provider keys live on the server only — never in the APK or in browser JavaScript.
- `GET /v1/health` reports capabilities and credential presence as booleans, never values.
- `GET /v1/languages` returns the capability matrix; use it to decide whether to offer
  voice output for a given language.
- The database is SQLite at `SMRITI_DB_PATH`. The caregiver app is the source of truth for
  family, medicines, appointments and routine — write those rows with `source='caregiver'`.
