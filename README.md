<div align="center">

# 🎙 SMRITI VoiceBot

**Project:** SMRITI · **Project ID:** SIH26003YELLOW · **Module:** AI Voice Assistant

<a href="#-the-idea"><img src="https://img.shields.io/badge/elder_care-voice_first-BC5A3C?style=for-the-badge&labelColor=201E1D" alt="Voice-first elder care"/></a>
<a href="#-languages"><img src="https://img.shields.io/badge/languages-15_configured-E8A83F?style=for-the-badge&labelColor=201E1D" alt="Languages"/></a>
<a href="#-security-model"><img src="https://img.shields.io/badge/safety-deterministic_in_code-56633F?style=for-the-badge&labelColor=201E1D" alt="Deterministic safety"/></a>

<br/>

<img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white&labelColor=474238" alt="Python 3.10+"/>
<img src="https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white&labelColor=474238" alt="FastAPI"/>
<img src="https://img.shields.io/badge/Uvicorn-1_worker-2E2E2E?labelColor=474238" alt="Uvicorn"/>
<img src="https://img.shields.io/badge/SQLite-persistent-003B57?logo=sqlite&logoColor=white&labelColor=474238" alt="SQLite"/>
<img src="https://img.shields.io/badge/Docker-AWS_EC2-2496ED?logo=docker&logoColor=white&labelColor=474238" alt="Docker on AWS EC2"/>
<img src="https://img.shields.io/badge/Groq-LLM-F55036?labelColor=474238" alt="Groq"/>
<img src="https://img.shields.io/badge/Sarvam-ASR_·_TTS-6C3EF4?labelColor=474238" alt="Sarvam"/>

<br/><br/>

**[The idea](#-the-idea)** ·
**[What it does](#-what-it-does)** ·
**[Architecture](#-architecture)** ·
**[How a turn is decided](#-how-a-turn-is-decided)** ·
**[API](#-api)** ·
**[Security](#-security-model)** ·
**[Languages](#-languages)** ·
**[Getting started](#-getting-started)** ·
**[Project layout](#-project-layout)**

</div>

---

## 🪢 The idea

SMRITI pairs an elderly parent with a tablet that listens, understands, and speaks back
in their own language — no screen-reading, no menus, no remembered commands. VoiceBot is
the part that makes "understands and speaks back" real. It is one FastAPI service with a
narrow job: turn a recorded question into a safe, personal, spoken answer. A separate
Backend owns the account and the family's data, and a separate Flutter client owns the
device — VoiceBot never sees either directly.

v5 is an **extension of v4.1, not a replacement**. The v4.1 command router still runs
first on every turn and still has final authority over actions — see `AUDIT.md` for the
migration record.

<table>
<tr>
<td width="34%" valign="top">

### 👵 For the elder

A question in Assamese, Hindi, or one of thirteen other configured languages gets a
spoken answer in the same language — from personal memory, the daily routine, or an
honest "I don't know," never a guess.

</td>
<td width="33%" valign="top">

### 🔌 For the Backend

One gateway, one contract. VoiceBot holds no Supabase session and authenticates nothing
about the end user — the Backend proxies every call and is the only thing that ever holds
the VoiceBot credential.

</td>
<td width="33%" valign="top">

### 🛟 For the safety-critical asks

Changing medication, moving money, or dialing an unknown number is refused
**deterministically, in code** — before any model ever sees the request. A model can
propose an action; it can never authorise one.

</td>
</tr>
</table>

**Integrating with the Backend or Flutter team?** `INTEGRATION_CONTRACT.md` is the
authoritative behavioral/ownership contract. For the practical walkthrough, start with
[`docs/VOICEBOT_INTEGRATION_GUIDE.md`](docs/VOICEBOT_INTEGRATION_GUIDE.md) — verified
against this codebase's actual behavior and the live deployment below.

---

## ✨ What it does

|     | Capability                    | What it actually does                                                                                                       |
| :-: | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| 💬  | **Text conversation**         | `POST /v1/conversation` — a turn in, a personalised reply out, session-aware                                                  |
| 🎙  | **Voice conversation**        | `POST /v1/conversation/voice` — spoken audio in, an async TTS job and a fetchable audio reply out                            |
| 🧠  | **Personal memory**           | Family, medicines and routine answered from a synced snapshot of the caregiver's data — never invented                       |
| 🛡  | **Deterministic safety**      | A prompt-injection screen and a hard-coded refusal list run **before** any model sees the utterance                          |
| 🧭  | **v4.1 command router**       | Still first in line on every turn, still has final authority over actions — v5 extends it, doesn't replace it                |
| 🧰  | **Validated tool calls**      | The model can propose a tool call; only the registry can execute one, and only after validation                              |
| 🗣  | **15 configured languages**   | ASR / LLM / TTS / offline-fallback capability reported per language, independently of each other                             |
| 📡  | **Offline fallback**          | With no network and no local LLM: family lookup, routine, medicine times, reminders and commands still work                  |
| 🔄  | **Memory sync**               | `POST /v1/memory/sync` — a revisioned, transactional snapshot replace; stale and conflicting revisions are rejected, not applied |
| 🧾  | **Legacy compatibility**      | `POST /v1/command` — the original v4.1 endpoint, kept for backward compatibility                                              |

---

## 🏛 Architecture

VoiceBot has **no end-user auth of its own and no Supabase session**. It sits behind the
Backend, which is the only party that ever holds its credential.

```mermaid
%%{init: {'theme':'base','themeVariables':{'primaryColor':'#F5EAD8','primaryBorderColor':'#BC5A3C','primaryTextColor':'#201E1D','lineColor':'#8C491A','fontFamily':'Segoe UI, Helvetica, sans-serif'}}}%%
flowchart LR
    F["📱 Flutter client<br/><i>recording · playback · UI</i><br/>never holds a VoiceBot credential"]
    B["🔐 Backend gateway<br/><i>Supabase auth · patient authorization</i><br/>holds the VoiceBot credential"]
    V["🎙 VoiceBot <i>(this repo)</i><br/><i>FastAPI · one Uvicorn worker</i>"]
    D[("🗄 SQLite + audio cache<br/>EBS-backed, persists across restarts")]

    subgraph EXT["🌍 Providers"]
        GROQ["🧠 Groq · qwen/qwen3.8-27b"]
        SARVAM["🗣 Sarvam · Saaras ASR / Bulbul v3 TTS"]
        PARLER["🔊 Indic Parler-TTS<br/>local, CPU · asm/brx/mni/npi"]
    end

    F -- "x-api-key never leaves here" --> B
    B -- "x-api-key" --> V
    V --> D
    V --> GROQ
    V --> SARVAM
    V --> PARLER
```

VoiceBot owns: ASR, conversation routing, personal memory, deterministic safety,
prompt-injection handling, action proposal/validation/confirmation, TTS, voice jobs,
temporary audio, language capability reporting, and its own API/deployment security. It
does not authenticate end users, does not talk to Supabase, and does not know about
Flutter. Full split in
[Ownership](docs/VOICEBOT_INTEGRATION_GUIDE.md#3-ownership).

---

## 🧭 How a turn is decided

```mermaid
%%{init: {'theme':'base','themeVariables':{'primaryColor':'#F5EAD8','primaryBorderColor':'#BC5A3C','primaryTextColor':'#201E1D','lineColor':'#8C491A','fontFamily':'Segoe UI, Helvetica, sans-serif'}}}%%
flowchart TD
    A["🗣 Utterance"] --> B{"Prompt-injection screen"}
    B -- "flagged" --> R1["⛔ Refuse — no model involved"]
    B -- "clean" --> C{"Deterministic safety screen"}
    C -- "medication · money · unknown number" --> R2["⛔ Refuse — before any model sees it"]
    C -- "clear" --> D{"Pending confirmation open?"}
    D -- "yes, explicit 'yes'" --> E2["✅ Resolve the pending action"]
    D -- "no" --> E{"v4.1 command router matches"}
    E -- "yes" --> F1["🧭 COMMAND — no model involved"]
    E -- "no" --> G["🧠 LLM + validated tools"]
    G --> H{"Model output"}
    H -- "conversation" --> I1["💬 CONVERSATION"]
    H -- "recalled fact" --> I2["🧠 MEMORY"]
    H -- "proposes an action" --> I3["🛟 CONFIRMATION requested"]
    G -- "no confident answer" --> J["📚 Deterministic fallback<br/>saved answers, no model"]

    style R1 fill:#FFF2EB,stroke:#B3402F
    style R2 fill:#FFF2EB,stroke:#B3402F
    style F1 fill:#F0FAE1,stroke:#56633F
```

The model can **propose**. Only the safety layer, the command router and the tool
registry can **authorise** — a model output never reaches a database write, the phone
dialer, or a medication change directly. Full detail in `ARCHITECTURE.md` and
`SECURITY.md`.

### 🗣 Voice job lifecycle

`POST /v1/conversation/voice` returns an immediate text response and, if there's
something to say, a `job_id` to poll.

```mermaid
%%{init: {'theme':'base','themeVariables':{'primaryColor':'#F5EAD8','primaryBorderColor':'#BC5A3C','primaryTextColor':'#201E1D','lineColor':'#8C491A','fontFamily':'Segoe UI, Helvetica, sans-serif'}}}%%
stateDiagram-v2
    direction LR
    [*] --> queued
    queued --> processing: one TTS worker, serial by design
    processing --> completed: audio_id fetchable once
    processing --> failed
    queued --> cancelled: POST /v1/voice/jobs/{id}/cancel
    processing --> cancelled
    completed --> [*]
    failed --> [*]
    cancelled --> [*]
```

Audio is never embedded in a JSON body or written to a log — a voice response carries an
opaque `audio_id`, and the client fetches the bytes once (ids expire, default 15 min).

---

## 🔌 API

| Method | Path                              | Auth   | Purpose                                                                                  |
| ------ | ---------------------------------- | ------ | ------------------------------------------------------------------------------------------ |
| `GET`  | `/v1/health`                      | public | Capability and connectivity report. Never returns a key.                                  |
| `GET`  | `/v1/languages`                   | public | The full capability matrix.                                                               |
| `GET`  | `/v1/languages/{code}`            | public | One language's capabilities.                                                              |
| `POST` | `/v1/conversation`                | key    | Text turn. `{user_id, message, session_id?, language?}`                                   |
| `POST` | `/v1/conversation/welcome`        | key    | Session-opening greeting.                                                                 |
| `POST` | `/v1/conversation/voice`          | key    | Voice turn. Multipart `audio_wav`, `user_id`, `session_id?`, `language?`, `speak?`        |
| `GET`  | `/v1/voice/jobs/{job_id}`         | key    | Poll an asynchronous voice-synthesis job.                                                 |
| `POST` | `/v1/voice/jobs/{job_id}/cancel`  | key    | Cancel a queued/processing job.                                                            |
| `GET`  | `/v1/audio/{id}`                  | key    | Fetch generated speech once. Ids expire (default 15 min).                                 |
| `POST` | `/v1/memory/sync`                 | key    | Full-snapshot caregiver-memory synchronization.                                            |
| `GET`  | `/v1/tools`                       | key    | Tool registry introspection.                                                              |
| `POST` | `/v1/command`                     | key    | **Legacy v4.1 endpoint** — kept for compatibility; new integrations should use the above.  |

See `docs/API_INTEGRATION.md` / `docs/integration/openapi.json` for full request/response
schemas.

> [!IMPORTANT]
> **Authentication fails closed.** Every personal-data endpoint requires `x-api-key`
> (never `Authorization: Bearer`) — the credential is server-side only and must never
> reach Flutter or a browser. Missing personal-endpoint configuration returns `503`, not
> open access. See [Security model](#-security-model).

---

## ☁️ Current live deployment

|                        |                                                                       |
| ---------------------- | ----------------------------------------------------------------------- |
| Base URL               | `https://15-206-144-216.nip.io`                                       |
| Region                 | AWS `ap-south-1`                                                       |
| Compute                | EC2 `m7i-flex.large`, Docker, one Uvicorn worker                       |
| Storage                | Persistent 30 GB EBS at `/data` (database, audio cache, model cache)   |
| HTTPS                  | Caddy, real Let's Encrypt certificate, survives reboot                 |
| Current live identity  | `elder-1` (single pilot identity — `elder-2`/`elder-3` are not active) |

See `docs/AWS_DEPLOYMENT.md` for the full architecture and `deployment/aws/` for the
provisioning/deploy/backup tooling.

| Role         | Provider                                          | Notes                                                                      |
| ------------ | ---------------------------------------------------- | ------------------------------------------------------------------------------ |
| General LLM  | Groq, `qwen/qwen3.8-27b`                            | `SMRITI_LLM_PROVIDER=groq`; real calls verified live                          |
| Cloud ASR    | Sarvam (Saaras family)                              | Real calls verified live against the deployment above                        |
| Cloud TTS    | Sarvam Bulbul v3                                    | Real calls verified live                                                     |
| Local TTS    | Indic Parler-TTS (`ai4bharat/indic-parler-tts`)     | asm/brx/mni/npi; real synthesis verified live, CPU, ~20–27s per short utterance |

Gemini, OpenAI and local-LLM interfaces remain available where explicitly configured but
are not the deployed path.
