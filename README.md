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

**Integrating with the Backend or Flutter team?** `INTEGRATION_CONTRACT.md` is the
**authoritative** behavioral/ownership contract — where any other document disagrees with
it, the contract wins. For the practical walkthrough, start with
[`docs/VOICEBOT_INTEGRATION_GUIDE.md`](docs/VOICEBOT_INTEGRATION_GUIDE.md)** — the master
integration reference, verified against this codebase's actual behavior and the live
deployment below, including exactly which documents each team should receive.
`docs/BACKEND_VOICEBOT_INTEGRATION.md` and `docs/FLUTTER_VOICEBOT_INTEGRATION.md` are the
step-by-step implementation guides for each team. Every other document in this repository
is supporting, historical, architectural, testing, or deployment reference — see the table
below for the full hierarchy.

---

## What SMRITI VoiceBot is, and why it exists

SMRITI pairs an elderly user with a tablet or device that listens, understands, and speaks
back in their own language — without asking them to read a screen, tap through menus, or
remember commands. VoiceBot is the service that makes the "understands and speaks back"
part work: it turns recorded speech into a safe, personalized, spoken reply, while a
separate Backend and Flutter client handle everything about the device, the caregiver's
data, and the family's account.

## Architecture

```
Flutter client (recording, playback, UI)
        │  (never holds a VoiceBot credential)
        ▼
Backend gateway (Supabase auth, patient authorization,
                  holds the VoiceBot credential, proxies calls)
        │  x-api-key
        ▼
VoiceBot (this repository) — a separate FastAPI service
```

VoiceBot owns: ASR, conversation routing, personal memory, deterministic safety,
prompt-injection handling, action proposal/validation/confirmation, TTS, voice jobs,
temporary audio, language capability reporting, and its own API/deployment security.
It does not authenticate end users, does not talk to Supabase, and does not know about
Flutter — those are the Backend's and Flutter's responsibility. See
[Ownership](docs/VOICEBOT_INTEGRATION_GUIDE.md#3-ownership) for the full split.

### How a turn is decided

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
registry can **authorise** — a model output never reaches a database write, the phone
dialer, or a medication change directly. Full detail in `ARCHITECTURE.md` and
`SECURITY.md`.

---

## Current live deployment

| | |
|---|---|
| Base URL | `https://15-206-144-216.nip.io` |
| Region | AWS `ap-south-1` |
| Compute | EC2 `m7i-flex.large`, Docker, one Uvicorn worker |
| Storage | Persistent 30 GB EBS at `/data` (database, audio cache, model cache) |
| HTTPS | Caddy, real Let's Encrypt certificate, survives reboot |
| Current live identity | `elder-1` (single pilot identity — `elder-2`/`elder-3` are not active) |

See `docs/AWS_DEPLOYMENT.md` for the full architecture and `deployment/aws/` for the
provisioning/deploy/backup tooling. `docs/AZURE_DEPLOYMENT.md` is an earlier, historical
evaluation — not the current target.

## Current providers

| Role | Provider | Notes |
|---|---|---|
| General LLM | Groq, `qwen/qwen3.8-27b` | `SMRITI_LLM_PROVIDER=groq`; real calls verified live |
| Cloud ASR | Sarvam (Saaras family) | Real calls verified live against the deployment above |
| Cloud TTS | Sarvam Bulbul v3 | Real calls verified live |
| Local TTS | Indic Parler-TTS (`ai4bharat/indic-parler-tts`) | asm/brx/mni/npi; real synthesis verified live, CPU, ~20–27s per short utterance on the current instance |

Gemini, OpenAI and local-LLM interfaces remain available where explicitly configured but
are not the deployed path.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/v1/health` | public | Capability and connectivity report. Never returns a key. |
| `GET` | `/v1/languages` | public | The capability matrix. |
| `GET` | `/v1/languages/{code}` | public | One language's capabilities. |
| `POST` | `/v1/conversation` | key | Text turn. `{user_id, message, session_id?, language?}` |
| `POST` | `/v1/conversation/welcome` | key | Session-opening greeting. |
| `POST` | `/v1/conversation/voice` | key | Voice turn. Multipart `audio_wav`, `user_id`, `session_id?`, `language?`, `speak?` |
| `GET` | `/v1/voice/jobs/{job_id}` | key | Poll an asynchronous voice-synthesis job. |
| `POST` | `/v1/voice/jobs/{job_id}/cancel` | key | Cancel a queued/processing job. |
| `GET` | `/v1/audio/{id}` | key | Fetch generated speech once. Ids expire (default 15 min). |
| `POST` | `/v1/memory/sync` | key | Full-snapshot caregiver-memory synchronization. |
| `GET` | `/v1/tools` | key | Tool registry introspection. |
| `POST` | `/v1/command` | key | **Legacy v4.1 endpoint** — kept for backward compatibility. New integrations should use the endpoints above, not this one. |

Audio is never embedded in a JSON body or written to a log. A voice response carries an
opaque `audio_id`; the client fetches the bytes once. See
`docs/API_INTEGRATION.md`/`docs/integration/openapi.json` for full request/response
schemas.

**Authentication fails closed.** All personal-data endpoints require `x-api-key` (never
`Authorization: Bearer`) — the VoiceBot credential is server-side only and must never
reach Flutter or a browser. `SMRITI_AUTH_USER_ID` binds a single-user deployment's
credential to one stable identity; `SMRITI_API_KEYS` supports multiple authorized
identities per key for a real Backend gateway. Missing personal-endpoint configuration
returns `503`, not open access. `SMRITI_ALLOW_UNAUTHENTICATED=1` only relaxes the key
check for local development.

## Voice job lifecycle

`POST /v1/conversation/voice` returns an immediate text response and (if `speak=true`
and there is something to say) a `job_id`. The client polls `GET
/v1/voice/jobs/{job_id}` until `status` is `completed`/`failed`/`cancelled`, then fetches
the audio once via `GET /v1/audio/{audio_id}`. Jobs can be cancelled mid-flight via
`POST /v1/voice/jobs/{job_id}/cancel`. One TTS worker processes jobs serially by design —
see [Operations](#operations) below.

## Memory synchronization

VoiceBot's personal memory is a **synchronized copy**, not the source of truth — Supabase
remains authoritative. `POST /v1/memory/sync` replaces a patient's family/medicine/routine
records as a full, transactional snapshot, keyed by an optional `source_revision`: an
older revision is rejected, an identical replay is a harmless no-op, and a same-revision
sync with different content is rejected as a conflict. See
[Memory synchronization](docs/VOICEBOT_INTEGRATION_GUIDE.md#7-memory-synchronization).

## Action semantics

The model can propose a tool call; a controlled action (calling, reminders, anything
`requires_confirmation`) is never executed on that proposal alone. `action_accepted: true`
means the deterministic layer authorized the action — **it is not proof of a real side
effect**. No real calling or reminder-scheduling executor exists in this repository by
design; both features are contract-ready but disabled pending Backend/Flutter building a
real executor.

## Language capability model

Every language in `/v1/languages` reports ASR/LLM/TTS/deterministic-fallback capability
and a separate `validated` flag **independently** — a provider being callable is not the
same claim as its output quality being validated by a native speaker.
`mni` is Meiteilon/Manipuri and is never mapped to Mongolian (`mn`). No language is
currently marked `validated: true` — see `LANGUAGE_SUPPORT.md`.

## What VoiceBot does NOT implement

Real phone calling, real conversational-reminder scheduling/delivery, clinical diagnosis,
prescription OCR, face recognition, clinical reporting, game scoring, and fully offline
general conversational AI are all confirmed absent — none is silently implied by anything
shipped here.

## Offline behaviour

With no network and no local LLM, these still work: family lookup, today's routine,
medicine times (read-only), visitors, reminders, games, and every command. General
knowledge and weather are refused honestly — never guessed. Reviewed offline wording
exists for English, Hindi, Assamese and Bengali; any other language's fallback says
plainly that it cannot answer, rather than replying in the wrong language.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # set SMRITI_API_KEY and SMRITI_AUTH_USER_ID
python main.py --test-config       # checks configuration, contacts nothing
pytest -q
```

## Running locally

```bash
python main.py --health                 # capability report
python main.py --list-languages         # v4.1 language packs
python main.py --seed-demo              # create the demo elder in SQLite
python main.py --chat                   # interactive text chat

uvicorn smriti_voice.api.app:app --host 0.0.0.0 --port 8000
python tools/smoke_test.py --base-url https://15-206-144-216.nip.io   # against the live deployment
```

## Testing and current verification status

```bash
pytest -q                      # full test suite: 625 passed
pytest tests/safety -q         # safety and red-team assertions
python -m compileall -q .
```

Every test in this repository's own suite is **mocked/deterministic** — none makes a live
provider call. Real-provider and real-deployment verification is separate and has been
performed against the live AWS deployment above: real Groq/Qwen conversation, real Sarvam
ASR, real Sarvam TTS, real Indic Parler-TTS synthesis, asynchronous voice jobs,
cancellation, protected audio retrieval, persistent memory and memory-revision handling,
patient isolation, safety/prompt-injection refusal, and restart/reboot recovery have all
been exercised live. See `EVALUATION.md` and `docs/RELEASE_ACCEPTANCE.md` for the
evidence-cited breakdown. **Native-speaker language quality validation has not been
performed for any language** — provider execution is not the same claim.

**Overall status: VoiceBot service is ready for Backend integration. Cross-system
integration (a real Backend gateway, a real Flutter client) and physical-device
acceptance remain pending** — see `docs/RELEASE_ACCEPTANCE.md`.

## Operations

- Sessions, pending confirmations, and history are all SQLite-persisted and survive a
  restart (`tests/integration/test_persistent_sessions.py`).
- One Uvicorn worker, by design: `SessionStore`, the rate limiter, and the Indic
  Parler-TTS model instance are all process-local; a second worker would silently break
  session/rate-limit isolation and load a second full model copy.
- Streaming/realtime voice is not implemented — this is request/response with async TTS.

## Documents

| File | Contents |
|---|---|
| `AUDIT.md` | The pre-migration audit of v4.1 and what happened to each module |
| `ARCHITECTURE.md` | Subsystems, turn flow, provider abstractions |
| `SECURITY.md` | Threat model, red-team results, what is enforced where |
| `LANGUAGE_SUPPORT.md` | Generated capability matrix — configured vs. speakable vs. validated |
| `EVALUATION.md` | What was tested, what was mocked, what was live, what was not tested |
| `HANDOFF.md` | Practical integration handoff for the Backend/Flutter teams |
| `INTEGRATION_CONTRACT.md` | The detailed API/ownership contract |
| `API_INTEGRATION.md` | Endpoint-by-endpoint request/response reference, audited against the live schemas |
| `BACKEND_APP_DEVELOPER_BACKGROUND.md` | Background and current deployment facts for the Backend developer |
| `STAGING_READINESS.md` | What is actually demonstrated for pilot/staging, with evidence |
| `CODEBASE_STATUS.md` | Component-by-component implementation status, dated |
| `TESTING.md` | The deterministic/mock test mode: what it proves and does not prove |
| `PROVISIONING_DESIGN.md` | Current pilot provisioning vs. a future scalable design (not implemented) |
| `CHANGELOG.md` | Notable changes, grouped by theme and dated from git history |
| `docs/VOICEBOT_INTEGRATION_GUIDE.md` | **Master integration reference — start here** |
| `docs/BACKEND_VOICEBOT_INTEGRATION.md` | Step-by-step for the Backend developer building the gateway |
| `docs/FLUTTER_VOICEBOT_INTEGRATION.md` | Step-by-step for the Flutter developer building the client |
| `docs/RELEASE_ACCEPTANCE.md` | Evidence-based acceptance matrix for every requirement |
| `docs/integration/CONTRACT_ACCEPTANCE_MATRIX.md` | All 19 integration-contract sections mapped to owner and status |
| `deployment/aws/` | Reproducible AWS provisioning/deploy/backup/rollback tooling |
| `docs/AWS_DEPLOYMENT.md` | The current, live AWS deployment — architecture, compute sizing, config, security, backup |
| `docs/AZURE_DEPLOYMENT.md` | Historical — Azure was evaluated before the target moved to AWS |
| `docs/integration/` | Machine-readable exports: OpenAPI, memory/action schemas, language matrix, error catalog |
