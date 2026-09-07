# SMRITI VoiceBot v5.0

**Project:** SMRITI · **Project ID:** SIH26003YELLOW · **Module:** AI Voice Assistant

A voice-first assistant for an elderly user. It answers ordinary spoken questions
("What is my daughter's name?", "What did I eat yesterday?", "How do I open WhatsApp?")
in the user's own language, works without the internet for saved information, and
refuses — deterministically, in code — to change medication, move money or dial an
unknown number.

v5 is an **extension of v4.1, not a replacement.** The v4.1 command router still runs
first on every turn and still has final authority over actions. See `AUDIT.md` for the
migration record.

---

## What actually works today

| Subsystem | State | Notes |
|---|---|---|
| Deterministic command router | **Working** | v4.1 logic unchanged; 66 regression assertions |
| Safety layer | **Working** | Config-driven, fails closed, 156 adversarial tests |
| Personal memory (SQLite) | **Working** | Provenance, per-user isolation |
| Tool registry | **Working** | Schema validation → safety → authorization → execute |
| Conversation manager | **Working** | Bounded history, confirmation, language following |
| Offline fallback | **Working** | Answers saved questions with no model at all |
| HTTP API | **Working** | Auth fails closed, upload validation, no audio in JSON |
| General LLM | **Groq / Qwen 3.8 27B** | Explicitly selected by `SMRITI_LLM_PROVIDER=groq`; other providers remain available |
| TTS provider | **Implemented, unverified** | Sarvam Bulbul — no live call has been made |
| Cloud ASR | **Implemented, unverified** | v4.1 Sarvam/OpenAI code, unchanged |
| Local ASR | **Benchmark-only** | Models not provisioned; engine refuses to load unvalidated packs |
| Local LLM / local TTS | **Not bundled** | Interfaces exist; point them at your own server |

**No language is marked SUPPORTED.** Nothing has been validated on real speech yet.
See `LANGUAGE_SUPPORT.md` and `EVALUATION.md`.

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # set SMRITI_API_KEY and SMRITI_AUTH_USER_ID
python main.py --test-config       # checks configuration, contacts nothing
pytest -q
```

`requirements.txt` installs only what the service and tests need. Local ASR
(`onnx-asr`, `transformers`, `torch`, `ne-lid`) is commented out — uncomment it on a
device that will actually run local models.

The deployed general conversational LLM is configured with `SMRITI_LLM_PROVIDER=groq`,
`GROQ_MODEL=qwen/qwen3.8-27b`, and a server-side `GROQ_API_KEY`.

## Running

```bash
python main.py --health                 # capability report
python main.py --list-languages         # v4.1 language packs
python main.py --seed-demo              # create the demo elder in SQLite
python main.py --chat                   # interactive text chat
python main.py --file utterance.wav --language eng   # v4.1 command path

uvicorn smriti_voice.api:app --host 0.0.0.0 --port 8080
python tools/smoke_test.py              # end-to-end, marks REAL vs LOCAL vs SKIP
```

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/health` | public | Capability and connectivity report. Never returns a key. |
| `GET` | `/v1/languages` | public | The capability matrix. |
| `GET` | `/v1/languages/{code}` | public | One language's capabilities. |
| `POST` | `/v1/conversation` | key | Text turn. `{user_id, message, session_id?, language?}` |
| `POST` | `/v1/conversation/voice` | key | Voice turn. Multipart `audio_wav`, `user_id`, `session_id?`, `language?`, `speak?` |
| `GET` | `/v1/audio/{id}` | key | Fetch generated speech. Ids expire (default 15 min). |
| `GET` | `/v1/tools` | key | Tool registry introspection. |
| `POST` | `/v1/command` | key | **v4.1 endpoint, unchanged contract.** Multipart `audio_wav`, `language`, `request_id?` |

Audio is never embedded in a JSON body or written to a log. A voice response carries an
opaque `audio_id`; the client fetches the bytes once.

**Authentication fails closed.** Personal conversation and voice endpoints require both
`SMRITI_API_KEY` and `SMRITI_AUTH_USER_ID`. The latter binds the authenticated API
credential to one stable user identity; caller-supplied IDs for another user are rejected.
The backward-compatible `/v1/command` endpoint remains API-key-only because it does not
access personal data. Missing personal-endpoint configuration returns `503`, not open
access. `SMRITI_ALLOW_UNAUTHENTICATED=1` only relaxes the key check for local development;
it does not remove identity binding from personal endpoints.

## How a turn is decided

```
utterance
  → prompt-injection screen        refuse
  → deterministic safety screen    refuse (before any model sees it)
  → pending confirmation?          resolve on an explicit yes only
  → v4.1 command router            COMMAND — no model involved
  → LLM + validated tools          CONVERSATION / MEMORY / CONFIRMATION
  → deterministic fallback         FALLBACK — saved answers, no model
```

The model can **propose**. Only the safety layer, the command router and the tool
registry can **authorise**. Full detail in `ARCHITECTURE.md` and `SECURITY.md`.

## Offline behaviour

With no network and no local LLM, these still work: family lookup, today's routine,
medicine times (read-only), visitors, reminders, games, and every command. General
knowledge and weather are refused honestly — never guessed.

Reviewed offline wording exists for English, Hindi, Assamese and Bengali. For any other
language the fallback says plainly that it cannot answer, rather than replying in the
wrong language.

No model weights ship with SMRITI. For offline general conversation, run an
OpenAI-compatible server locally and set `SMRITI_LOCAL_LLM_URL`.

## Testing

```bash
pytest -q                      # full test suite
pytest tests/safety -q         # 156 safety and red-team assertions
pytest tests/legacy -q         # the original v4.1 suite, unmodified
python -m compileall -q .
```

Every provider test is **mocked**. No test in this repository makes a live provider call,
and none asserts a fabricated provider result.

## Limitations

- Nothing has been validated on real speech. `EVALUATION.md` records what was and was not tested.
- Groq/Qwen is the configured general LLM for deployment. Gemini, OpenAI and Sarvam
  integrations remain available where explicitly configured.
- Sarvam Bulbul does not speak Assamese, Bodo, Manipuri, Nepali or any Northeast language.
- Local ASR models are benchmark-only and are not provisioned.
- Sarvam tool calling is unverified; the router will not send it a tool-bearing turn unless
  `SARVAM_TOOLS_VERIFIED=1`.
- Streaming/realtime voice is not implemented — this is request/response.
- Sessions live in memory; a restart loses in-flight confirmations (durable history is in SQLite).

## Documents

| File | Contents |
|---|---|
| `AUDIT.md` | The pre-migration audit of v4.1 and what happened to each module |
| `ARCHITECTURE.md` | Subsystems, turn flow, provider abstractions |
| `SECURITY.md` | Threat model, red-team results, what is enforced where |
| `LANGUAGE_SUPPORT.md` | Generated capability matrix |
| `EVALUATION.md` | What was tested, what was mocked, what was not tested |
| `HANDOFF.md` | Integration notes for the app and backend developers |
