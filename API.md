# API contract

Base URL: `http://<host>:8080` · Version: `5.0.0` · Content type: `application/json` unless stated.

Every example below was captured from the running service, not written from memory.

---

## Authentication

| Header | Value |
|---|---|
| `x-api-key` | must equal the server's `SMRITI_API_KEY` |
| `x-request-id` | *optional.* Alphanumeric, ≤64 chars. Echoed back as `request_id`. Ignored if malformed. |

Personal endpoints also require `SMRITI_AUTH_USER_ID` on the server. The submitted
`user_id` must exactly match that authenticated identity; a caller cannot select another
user's memory, family, medication, reminders, or session by changing JSON/form fields.

**Fails closed.** Comparison uses `hmac.compare_digest`.

| Condition | Status |
|---|---|
| Key correct | 200 |
| Key missing or wrong | **401** |
| No `SMRITI_API_KEY` configured on the server | **503** (not open access) |
| No `SMRITI_AUTH_USER_ID` configured on the server | **503** |
| `user_id` does not match the authenticated identity | **403** |
| More than `SMRITI_RATE_LIMIT_PER_MINUTE` requests (default 60) | **429** |

`GET /v1/health`, `GET /v1/languages` and `GET /v1/languages/{code}` are public. Everything else requires the key.

Provider API keys (Sarvam, Gemini, OpenAI) live on the server only. They must never be compiled into an APK, a Flutter bundle or browser JavaScript.

---

## Endpoint summary

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/health` | public | Capability, connectivity and configuration report |
| `GET` | `/v1/languages` | public | Full capability matrix |
| `GET` | `/v1/languages/{code}` | public | One language |
| `POST` | `/v1/conversation` | key | Text turn |
| `POST` | `/v1/conversation/voice` | key | Voice turn (multipart) |
| `GET` | `/v1/audio/{audio_id}` | key | Fetch generated speech |
| `GET` | `/v1/tools` | key | Tool registry introspection |
| `POST` | `/v1/command` | key | **v4.1 command endpoint — contract unchanged** |

---

## POST /v1/conversation

The main endpoint. Use it for text input and to test the whole stack without audio.

**Request**

```json
{
  "user_id": "elder-1",
  "session_id": "1850c051c5514df08c59556710592caa",
  "message": "What is my daughter's name?",
  "language": "eng"
}
```

| Field | Type | Required | Rules |
|---|---|---|---|
| `user_id` | string | **yes** | 1–64 chars, letters/digits/`-`/`_`/`.` only. Anything else → **422** |
| `session_id` | string | no | Same charset. Omit on the first turn; send back what you receive thereafter |
| `message` | string | **yes** | 1–2000 chars |
| `language` | string | no | Internal code (`eng`, `hin`, `asm`, `ben`, …). Omitted → detected from the text. Unknown → **400** |

**Response** (actual output for `"open play"`):

```json
{
  "request_id": "3034184e90f2412dae8465261e44f695",
  "session_id": "1850c051c5514df08c59556710592caa",
  "response_text": "Opening your games now.",
  "language": "eng",
  "language_confidence": 0.0,
  "kind": "COMMAND",
  "action": "OPEN_PLAY",
  "action_accepted": true,
  "tool_calls": [],
  "tool_results": [],
  "safety": null,
  "requires_confirmation": false,
  "metadata": {
    "request_id": "3034184e90f2412dae8465261e44f695",
    "session_id": "1850c051c5514df08c59556710592caa",
    "kind": "COMMAND",
    "execution_mode": "DEGRADED",
    "offline": true,
    "fallback_used": false,
    "asr_provider": null, "asr_latency_ms": 0,
    "llm_provider": null, "llm_latency_ms": 0,
    "tool_latency_ms": 0,
    "tts_provider": null, "tts_latency_ms": 0,
    "total_latency_ms": 1,
    "error_code": null
  }
}
```

### `kind` — how the answer was produced

| Value | Meaning | What the UI should do |
|---|---|---|
| `COMMAND` | The deterministic v4.1 router matched. No model involved. | Execute `action` |
| `CONVERSATION` | The model answered from general knowledge | Show/speak `response_text` |
| `MEMORY` | The model answered using the user's saved data via a tool | Show/speak `response_text` |
| `CONFIRMATION` | A controlled action is waiting for yes/no, **or** one has just resolved | Show large **Yes** / **No** if `requires_confirmation` |
| `REFUSAL` | The safety layer refused | Show/speak `response_text`. Never retry automatically |
| `FALLBACK` | No model was reachable; answered from saved data | Show/speak `response_text` |
| `ERROR` | Could not serve the turn | Show `response_text`, offer the touch fallback |

### `action` — the only executable values

`OPEN_PLAY`, `OPEN_MY_PEOPLE`, `OPEN_TODAY`, `OPEN_MEDICINE`, `HELP`, `STOP`, `CALL_PRIMARY_CONTACT`, `CALL_BINA`, `NO_ACTION`

> **Execute an action only when `action_accepted` is `true`.**
> Never execute `response_text`, a transcript, a URL or anything else as a command.
> `NO_ACTION` always means do nothing.

### `safety` — present only on a refusal

```json
{
  "allowed": false,
  "reason": "sensitive_verb_noun_pair",
  "category": "medication",
  "refusal_key": "medication",
  "matched": ["delete", "medicine"]
}
```

`category` ∈ `medication`, `financial`, `record_deletion`, `unknown_number`, `caregiver_permissions`, `safety_override`, `prompt_injection`, `authorization`.

### `metadata.execution_mode`

| Value | Meaning |
|---|---|
| `ONLINE_PRIMARY` | A cloud provider answered |
| `OFFLINE_PRIMARY` | A local model answered |
| `DEGRADED` | Deterministic local answer — commands, memory, routine. **A working state** |
| `ERROR` | Nothing could serve the turn |

---

## The confirmation flow

Controlled actions (placing a call, creating a reminder, starting a game) are **never** executed from a single utterance.

```
POST /v1/conversation  {"user_id":"elder-1","message":"How do I call Bina?"}
→ kind="CONFIRMATION", requires_confirmation=true, action="NO_ACTION"
  response_text="Would you like me to call Bina now? Please say yes or no."

POST /v1/conversation  {"user_id":"elder-1","session_id":"<same>","message":"Yes"}
→ kind="CONFIRMATION", action="CALL_PRIMARY_CONTACT", action_accepted=true
  response_text="Calling Bina now."
```

Rules the client must respect:

1. **You must send `session_id`** on the second turn, or the pending action is lost.
2. Anything that is not a clear affirmation leaves it pending — `requires_confirmation` stays `true`. Ask again.
3. A clear negation (`"no"`, `"नहीं"`) cancels it.
4. Confirmations expire after ~3 minutes and on session idle timeout. **Never auto-confirm on a timeout.**
5. Affirmations are recognised in English, Hindi, Assamese and Bengali (`config/safety.json`).

---

## POST /v1/conversation/voice

`multipart/form-data`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `audio_wav` | file | **yes** | WAV, `audio/wav` \| `audio/x-wav` \| `audio/wave` \| `application/octet-stream` |
| `user_id` | string | **yes** | |
| `session_id` | string | no | |
| `language` | string | no | Omit to auto-detect |
| `speak` | bool | no | Default `true`. `false` returns text only |

Upload validation (checked before any decoder touches the bytes):

| Condition | Status |
|---|---|
| Empty file | **400** |
| Larger than `max_upload_bytes` (default 10 MB) | **413** |
| Not RIFF/WAVE, truncated, or missing a `data` chunk | **415** |
| Wrong `Content-Type` | **415** |

**Response** — everything from `/v1/conversation` plus:

| Field | Type | Meaning |
|---|---|---|
| `transcript` | string | What ASR heard |
| `audio_id` | string \| null | Opaque 32-char hex handle |
| `audio_url` | string \| null | `/v1/audio/{audio_id}` |
| `audio_available` | bool | **If `false`, show text only** |
| `audio_unavailable_reason` | string \| null | See below |
| `tts_provider` | string \| null | Which provider spoke |

`audio_unavailable_reason` values:

| Value | Meaning | UI response |
|---|---|---|
| `NO_TTS_PROVIDER_SUPPORTS_LANGUAGE` | **No configured provider can ever speak this language** (e.g. Assamese). The service refuses to substitute another language | Show text. Consider a "voice not available in this language" note |
| `TTS_UNAVAILABLE` | A provider covers the language but the call failed now | Show text; voice may work on retry |
| `EMPTY_RESPONSE_TEXT` | Nothing to speak | — |
| `TTS_NOT_REQUESTED` | `speak=false` | — |

Voice-specific `metadata.error_code`:

| Code | Meaning |
|---|---|
| `ASR_UNAVAILABLE` | No ASR provider could transcribe. `response_text` asks the user to try again, in their language |
| `NO_SPEECH_DETECTED` | Audio contained no speech |

**Audio never appears in a JSON body or a log.** The response carries the id; the bytes are fetched once.

---

## GET /v1/audio/{audio_id}

Returns `audio/wav` bytes. Requires the API key.

| Condition | Status |
|---|---|
| Found | 200, `audio/wav` |
| Unknown, expired, or not 32 hex chars | **404** |
| No/wrong API key | **401** (checked before lookup, so ids cannot be enumerated) |

Files expire after `SMRITI_AUDIO_RETENTION_MINUTES` (default 15). **Fetch promptly and cache client-side if replay is needed.**

---

## GET /v1/health

Public. **Never returns a credential** — presence is reported as booleans.

```json
{
  "status": "ok",
  "version": "5.0.0",
  "connectivity": {"online": false, "detail": "TimeoutError", "forced_offline": false,
                   "checked_seconds_ago": 3},
  "providers": {
    "credentials_configured": {"sarvam": false, "gemini": false, "openai": false},
    "llm": {"local": false, "gemini": false, "openai": false, "sarvam": false, "mock": true},
    "tts": {"sarvam": false, "local": false, "mock": true}
  },
  "capabilities": {
    "deterministic_commands": true,
    "personal_memory": true,
    "general_conversation": false,
    "offline_general_conversation": false,
    "voice_output": false,
    "offline_voice_output": false
  },
  "languages": {"configured": 15, "by_status": {"BENCHMARK_ONLY": 7, "NOT_YET_TESTED": 7,
                "UNSUPPORTED": 1}, "validated": 0, "speakable": 3},
  "tools": {"registered": 21},
  "configuration": {"offline_forced": false, "authentication_configured": true,
                    "unauthenticated_access_allowed": false,
                    "max_upload_bytes": 10485760, "max_history_turns": 8}
}
```

`capabilities.voice_output` is `true` only when a provider is configured **and** it can speak at least one configured language. Use it to decide whether to offer voice at all.

---

## GET /v1/languages

```json
{
  "count": 15,
  "summary": {"SUPPORTED": 0, "BENCHMARK_ONLY": 7, "NOT_YET_TESTED": 7, "UNSUPPORTED": 1},
  "note": "A language is reported SUPPORTED only after a measured validation run.",
  "languages": [ { "code": "asm", "name": "Assamese", "script": "Bengali-Assamese",
                   "asr_online": true, "asr_offline": true, "llm_support": true,
                   "tts_online": false, "tts_provider": null, "tts_voice": null,
                   "language_detection": true, "status": "NOT_YET_TESTED",
                   "validated": false, "known_limitations": ["..."] } ]
}
```

**Use `tts_online` to decide whether to offer voice output for a language.** For Assamese it is `false`: Sarvam Bulbul does not document Assamese, so the assistant can hear and reason but not speak.

`status` ∈ `SUPPORTED`, `SUPPORTED_WITH_LIMITATIONS`, `ONLINE_ONLY`, `OFFLINE_ONLY`, `BENCHMARK_ONLY`, `NOT_YET_TESTED`, `UNSUPPORTED`. **Nothing is `SUPPORTED` until a measured validation record exists.**

---

## POST /v1/command  (v4.1, unchanged)

Existing clients keep working. The **only** change is that the API key is now required.

`multipart/form-data`: `audio_wav` (file), `language` (string, required), `request_id` (optional).

```json
{
  "request_id": "...", "transcript": "open play", "language": "eng",
  "language_confidence": 1.0, "intent": "OPEN_PLAY", "action": "OPEN_PLAY",
  "accepted": true, "confidence": 1.0, "reason": "accepted",
  "latency_ms": 812, "offline": true, "provider": "indicconformer"
}
```

Same rule as before: execute `action` only when `accepted` is `true`.

---

## Error format

Typed errors return a stable code:

```json
{"error": "LANGUAGE_NOT_SUPPORTED", "detail": "'klingon' is not a configured SMRITI language"}
```

Codes: `CONFIGURATION_ERROR`, `PROVIDER_ERROR`, `PROVIDER_NOT_CONFIGURED`, `PROVIDER_TIMEOUT`, `LLM_ERROR`, `TTS_ERROR`, `WEATHER_ERROR`, `AUDIO_ERROR`, `LANGUAGE_NOT_SUPPORTED`, `TOOL_NOT_FOUND`, `TOOL_VALIDATION_ERROR`, `SAFETY_REFUSAL`, `NOT_AUTHORIZED`, `MEMORY_NOT_FOUND`.

FastAPI validation failures return **422** in the standard FastAPI shape.

---

## Integration checklist for the backend developer

- [ ] Set `SMRITI_API_KEY`. Without it every protected endpoint returns 503.
- [ ] Never set `SMRITI_ALLOW_UNAUTHENTICATED=1` on a network-facing host.
- [ ] Keep provider keys server-side only.
- [ ] Persist `session_id` per user and send it on every turn.
- [ ] Execute only allow-listed `action` values, only when `action_accepted` is `true`.
- [ ] Never execute `response_text` or `transcript`.
- [ ] Handle `requires_confirmation` with a large Yes/No. Never auto-confirm.
- [ ] Check `audio_available` before attempting playback; fall back to text.
- [ ] Fetch `audio_url` promptly — it expires in 15 minutes.
- [ ] Poll `/v1/languages` to decide which languages to offer voice for.
- [ ] Keep medicine editing entirely out of the voice path — the API will refuse it anyway.
- [ ] The caregiver app writes `family_members`, `medicines`, `appointments`, `daily_routines`
      with `source='caregiver'`. It is the source of truth.
