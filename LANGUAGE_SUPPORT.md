# Language support

**This file is generated from the live capability matrix** by
`python -c "from smriti_voice.language.registry import LanguageService"` — see
`smriti_voice/language/capabilities.py`. It is regenerated whenever the matrix changes,
so it cannot drift away from what the code actually does.

## The rule

A language is **never** reported as `SUPPORTED` because a vendor's documentation lists it.
Vendor coverage produces a *capability* (`asr_online = true`). The **status** stays at
`NOT_YET_TESTED` until `config/language_validation.json` carries a record produced by a real,
measured test run on real speech.

At the time of writing **no language has such a record**, so nothing is marked `SUPPORTED`.
That is the honest position, not a bug.

## Status values

| Status | Meaning |
|---|---|
| `SUPPORTED` | Measured end-to-end: ASR, LLM and TTS all validated |
| `SUPPORTED_WITH_LIMITATIONS` | Validated, but missing TTS or LLM coverage |
| `ONLINE_ONLY` | Validated, but only with a network connection |
| `OFFLINE_ONLY` | Validated locally; no cloud provider covers it |
| `BENCHMARK_ONLY` | A model exists but the engine refuses to load it in production |
| `NOT_YET_TESTED` | Capability exists, nobody has measured it |
| `UNSUPPORTED` | No ASR path of any kind |

## Matrix

| Code | Language | Script | Status | ASR online | ASR offline | LLM | TTS | Detection |
|---|---|---|---|---|---|---|---|---|
| `asm` | Assamese | Bengali-Assamese | `NOT_YET_TESTED` | yes | yes | yes | — | yes |
| `ben` | Bengali | Bengali | `NOT_YET_TESTED` | yes | yes | yes | yes | yes |
| `brx` | Bodo | Devanagari | `NOT_YET_TESTED` | yes | yes | — | — | yes |
| `ccp` | Chakma | Chakma | `BENCHMARK_ONLY` | — | — | — | — | — |
| `eng` | English | Latin | `NOT_YET_TESTED` | yes | — | yes | yes | yes |
| `grt` | Garo | Latin | `BENCHMARK_ONLY` | — | — | — | — | yes |
| `hin` | Hindi | Devanagari | `NOT_YET_TESTED` | yes | yes | yes | yes | yes |
| `kha` | Khasi | Latin | `BENCHMARK_ONLY` | — | — | — | — | yes |
| `lus` | Mizo | Latin | `BENCHMARK_ONLY` | — | — | — | — | yes |
| `mni` | Meitei (Manipuri) | Meitei/Bengali | `NOT_YET_TESTED` | yes | yes | — | — | yes |
| `nag` | Nagamese | Latin | `BENCHMARK_ONLY` | — | — | — | — | yes |
| `npi` | Nepali | Devanagari | `NOT_YET_TESTED` | yes | yes | yes | — | yes |
| `nyish` | Nyishi | Latin | `UNSUPPORTED` | — | — | — | — | yes |
| `trp` | Kokborok (Tripuri) | Latin | `BENCHMARK_ONLY` | — | — | — | — | yes |
| `wao` | Wancho | Latin | `BENCHMARK_ONLY` | — | — | — | — | — |

## Providers behind each column

| Column | Provider | Coverage |
|---|---|---|
| ASR online | Sarvam Saaras (`saaras:v4`) | 22 Indic languages + English |
| ASR offline | AI4Bharat IndicConformer (ONNX) / MWirelabs NE-ASR | 6 packs enabled, 7 benchmark-only |
| LLM | Groq / Qwen 3.8 27B (deployment default); Gemini / OpenAI / Sarvam / local remain available | Conservative list; Northeast languages excluded pending measurement |
| TTS | Sarvam Bulbul (`bulbul:v3`) | **10 Indian languages + English only** |
| Detection | Sarvam auto-detect, NE-LID, Unicode script | — |

## The Assamese gap

Sarvam's **ASR** covers Assamese. Sarvam's **TTS (Bulbul) does not** — its documented list is
Hindi, Bengali, Kannada, Malayalam, Marathi, Odia, Punjabi, Tamil, Telugu, Gujarati and English.

So for Assamese (and Bodo, Manipuri, Nepali and every Northeast language) SMRITI can *hear* and
*reason* but cannot *speak*. The TTS router returns
`available = false, unavailable_reason = "NO_TTS_PROVIDER_SUPPORTS_LANGUAGE"` and the UI must
show the answer as text. It never substitutes a different language's voice.

To close the gap, configure a local TTS server (`SMRITI_LOCAL_TTS_URL`) and declare the languages
it genuinely covers in `SMRITI_LOCAL_TTS_LANGUAGES`.

## Known limitations, per language

**Assamese (`asm`)**
- Sarvam Bulbul TTS does not list Assamese: cloud voice output is unavailable.
- Assamese and Bengali share a script, so offline script detection cannot separate them unless the text contains an Assamese-only letter. Detection relies on NE-LID or the provider hint.
- Speech input works online, but no configured provider can speak this language.
- No measured end-to-end validation record exists for this language.

**Bengali (`ben`)**
- No measured end-to-end validation record exists for this language.

**Bodo (`brx`)**
- No documented cloud TTS.
- LLM generation quality not measured.
- Speech input works online, but no configured provider can speak this language.
- No measured end-to-end validation record exists for this language.

**Chakma (`ccp`)**
- No cloud ASR or TTS. Local NE-ASR model is benchmark-only.
- Local model is benchmark-only: not validated on device with native speakers.
- No measured end-to-end validation record exists for this language.

**English (`eng`)**
- Local model is benchmark-only: not validated on device with native speakers.
- No measured end-to-end validation record exists for this language.

**Garo (`grt`)**
- No cloud ASR or TTS. Local NE-ASR model is benchmark-only.
- Local model is benchmark-only: not validated on device with native speakers.
- No measured end-to-end validation record exists for this language.

**Hindi (`hin`)**
- No measured end-to-end validation record exists for this language.

**Khasi (`kha`)**
- No cloud ASR or TTS. Local NE-ASR model is benchmark-only.
- Local model is benchmark-only: not validated on device with native speakers.
- No measured end-to-end validation record exists for this language.

**Mizo (`lus`)**
- No cloud ASR or TTS. Local NE-ASR model is benchmark-only.
- Local model is benchmark-only: not validated on device with native speakers.
- No measured end-to-end validation record exists for this language.

**Meitei (Manipuri) (`mni`)**
- No documented cloud TTS.
- LLM generation quality not measured.
- Command pack ships with no phrases, so the command router fails closed.
- Speech input works online, but no configured provider can speak this language.
- No measured end-to-end validation record exists for this language.

**Nagamese (`nag`)**
- No cloud ASR or TTS. Local NE-ASR model is benchmark-only.
- Local model is benchmark-only: not validated on device with native speakers.
- No measured end-to-end validation record exists for this language.

**Nepali (`npi`)**
- No documented cloud TTS.
- Speech input works online, but no configured provider can speak this language.
- No measured end-to-end validation record exists for this language.

**Nyishi (`nyish`)**
- No ASR of any kind is shipped. Voice input is unavailable; use the touch fallback.
- No local ASR is shipped for this language.
- No measured end-to-end validation record exists for this language.

**Kokborok (Tripuri) (`trp`)**
- No cloud ASR or TTS. Local NE-ASR model is benchmark-only.
- Local model is benchmark-only: not validated on device with native speakers.
- No measured end-to-end validation record exists for this language.

**Wancho (`wao`)**
- No cloud ASR or TTS. Local NE-ASR model is benchmark-only.
- Local model is benchmark-only: not validated on device with native speakers.
- No measured end-to-end validation record exists for this language.

## Adding a validated language

1. Collect real speech from native speakers on the target device.
2. Run `tools/smoke_test.py --language <code>` with live credentials.
3. Measure WER/CER, command accuracy, false-activation rate and p95 latency.
4. Append a record to `config/language_validation.json` with the date, sample count,
   measured numbers and the operator's name.
5. Regenerate this file and re-run the test suite.

Hand-editing the validation file to claim support without a measurement is a falsification of
the evaluation record.
