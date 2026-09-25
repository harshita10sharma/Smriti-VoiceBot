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
