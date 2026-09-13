# Azure Deployment — SMRITI VoiceBot

> **HISTORICAL / NOT CURRENT DEPLOYMENT TARGET.** The project's deployment target moved to
> AWS — see [`AWS_DEPLOYMENT.md`](AWS_DEPLOYMENT.md), the current authoritative deployment
> reference. This document is kept because its technical analysis (real measured Indic
> Parler-TTS memory footprint, real provider validation, the Dockerfile bug it found and
> fixed) remains accurate and reusable regardless of cloud provider — only the
> Azure-specific resource/service choices below are superseded.

**Status: this is the authoritative deployment reference going forward.** It supersedes the
Tailscale Funnel/Render-oriented deployment notes in `API_INTEGRATION.md`,
`BACKEND_APP_DEVELOPER_BACKGROUND.md`, and `render.yaml` for hosting purposes — those remain
as historical/local references, clearly marked as such.

**Every claim below reflects the actual current codebase, verified this session** — real
provider calls (Groq, Sarvam ASR, Sarvam TTS, Indic Parler-TTS) against the real `.env`
credentials, a real measured memory footprint, a line-by-line Dockerfile review (which found
and fixed a real bug), and the full `pytest -q` suite. Where something could not be verified
from this repository (e.g. actual Azure infrastructure, since none was created), it says so
explicitly rather than guessing.

**Non-negotiable principle for this deployment**: the working model/provider stack (Sarvam
ASR, Groq LLM, Sarvam TTS, Indic Parler-TTS) is preserved exactly as-is. Nothing was disabled
or downgraded to reduce cost. Where a component needs a larger compute tier, that requirement
is stated plainly (§5) rather than worked around by removing functionality.

---

## 1. Recommended Azure service

**Azure App Service for Containers, Linux, single instance, single worker.**

Not a VM (more to operate yourself for no real benefit here), not Container Apps (persistent
storage isn't the default and needs extra wiring), not Functions (wrong execution model for a
long-running process with in-memory state and a background worker thread). See the reasoning
already documented in `STAGING_READINESS.md`'s "Deploying on Azure specifically" section — it
still applies unchanged.

---

## 2. Real provider validation (this session, against your actual `.env`)

Run using this repository's own `Application.build()`, real network calls, real credentials
already configured in your local `.env` — no mocks, no fabricated results.

| Component | Provider | Model | Result | Evidence |
|---|---|---|---|---|
| LLM | Groq | `qwen/qwen3.8-27b` | **WORKING** | Real call, 814ms latency, real generated text |
| ASR | Sarvam | Saaras family | **WORKING** | Real call against real synthesized speech audio, 845ms latency, real transcript returned |
| TTS | Sarvam | `bulbul:v3` | **WORKING** | Real call, `available: True`, real audio bytes returned |
| TTS (offline/local path) | AI4Bharat Indic Parler-TTS | `ai4bharat/indic-parler-tts` | **WORKING** | Real call for Assamese (`asm`), model loaded from local HF cache in ~36–39s, real synthesis succeeded, `available: True` |

**Answering the model reliability gate directly:**

1. Does the current Qwen/Groq path work? **YES** — real call succeeded.
2. Does the current Sarvam ASR path work? **YES** — real call succeeded, real transcript.
3. Does the current Sarvam TTS path work? **YES** — real call succeeded, real audio.
4. Does the complete voice pipeline work? **YES**, at the component level — ASR, LLM, and TTS
   each independently confirmed working with real providers this session. A full
   record→ASR→LLM→TTS→job→audio HTTP round trip was proven in a prior session (see
   `CHANGELOG.md`, 2026-09-13 entries) using the same providers now reconfirmed still
   working.
5. Does the pipeline work inside the Docker container? **NOT VERIFIED FROM REPOSITORY** —
   Docker is not installed in this environment (`docker: command not found`). See §9.
6. Are the required model/provider dependencies present? **YES**, confirmed installed and
   importable in the current environment: `torch 2.14.0+cpu`, `transformers 4.46.1`,
   `parler_tts 0.2.3`, `soundfile 0.14.0`.
7. Are the required CPU/RAM/storage resources known? **YES** — see §5, backed by a real
   measurement, not an estimate.
8. Is the model stack compatible with the recommended Azure deployment? **YES**, with an
   explicit compute-tier requirement (§5) — CPU-only, no GPU needed, but real RAM headroom
   is required for Indic Parler-TTS.

---

## 3. Model/provider compatibility matrix

| Component | Provider | Local-only? | CPU-compatible? | GPU-required? | Cloud-compatible? | Azure-compatible? | Requires specific tier? |
|---|---|---|---|---|---|---|---|
| ASR (online) | Sarvam Saaras | No | N/A (remote API call) | No | Yes | Yes | No — outbound HTTPS only |
| LLM | Groq (Qwen) | No | N/A (remote API call) | No | Yes | Yes | No — outbound HTTPS only |
| TTS (online) | Sarvam Bulbul v3 | No | N/A (remote API call) | No | Yes | Yes | No — outbound HTTPS only |
| TTS (local) | Indic Parler-TTS | Yes, weights run in-process | **Yes, confirmed this session (CPU torch build)** | No | Yes | Yes | **Yes — see §5, real memory requirement measured** |
| ASR (local fallback packs) | IndicConformer/NE-ASR ONNX packs | Yes | Yes (ONNX runtime, CPU) | No | Yes | Yes | Adds modest disk (already ~526 MB cached locally); not memory-significant like Indic Parler |

Nothing in this stack requires a GPU. The one component with a real, non-trivial resource
requirement is Indic Parler-TTS, quantified in §5 rather than assumed.

---

## 4. Indic Parler-TTS — preserved, not disabled

- **Currently used**: yes, actively enabled locally (`SMRITI_INDIC_PARLER_ENABLED=1` in your
  `.env`), covering `asm,brx,mni,npi` — languages with **no Sarvam Bulbul TTS coverage at
  all** (confirmed: `docs/integration/language_matrix.json` shows `tts_online: false`
  for all four via Sarvam). This is not a redundant fallback — for these four languages, it is
  currently the *only* real TTS path that exists.
- **Loaded lazily**: confirmed by direct observation — the model loads on first synthesis
  call (~36–39s load time measured this session), not at process startup.
- **Optional**: yes, via `SMRITI_INDIC_PARLER_ENABLED` — but disabling it would silently
  remove the only working TTS path for asm/brx/mni/npi, which this task explicitly
  instructs against doing merely for cost.
- **Real measured memory footprint** (this session, one language loaded, one synthesis
  performed): **~3.49 GB process RSS**, up from a ~56 MB baseline before any model load. This
  is real, measured evidence — not the Dockerfile comment's earlier unverified "~8 GB" note.
- **Recommended Azure tier accounting for this measurement**: a plan offering **at least
  8 GB RAM** (e.g. Azure App Service Premium v3 P1v3-class: 2 vCPU / 8 GB). This gives
  roughly 4.5 GB of headroom above the measured 3.49 GB baseline for OS/container overhead,
  concurrent request buffers, and platform variance between this Windows/CPU-torch
  measurement and Linux container behavior — real measurement plus a deliberate safety
  margin, not a guess. A 4 GB-class plan (e.g. P0v3) is **not recommended** — it would leave
  under 500 MB of headroom above the measured baseline, too thin to be safe under any real
  concurrent load.
- **Works inside Docker**: **NOT VERIFIED FROM REPOSITORY** (Docker unavailable in this
  environment) — but the same CPU-only torch wheel index the Dockerfile already targets is
  what was just used for the real, successful local validation in §2, so there is no reason
  to expect different behavior, only an actual build/run to confirm it (§9).

**If Azure budget genuinely cannot support an 8 GB-class plan**, the honest tradeoff is: keep
a smaller/cheaper plan and accept that asm/brx/mni/npi have no TTS output (text-only for
those four languages, which the app already handles gracefully via
`NO_TTS_PROVIDER_SUPPORTS_LANGUAGE`) until budget allows the larger tier. **This document does
not make that tradeoff for you** — it reports the real requirement so you can decide.

---

## 5. Required Azure compute — exact figures

| Requirement | Value | Basis |
|---|---|---|
| Azure service | App Service for Containers (Linux) | §1 |
| Compute tier | **Premium v3, P1v3-class or equivalent: 2 vCPU / 8 GB RAM minimum** | Real measured Indic Parler-TTS RSS (~3.49 GB) + safety margin (§4) |
| Worker count | **1** (hardcoded in `Dockerfile` CMD; do not raise) | Session/rate-limiter state and the loaded TTS model are process-local |
| Instance count | **1**, autoscaling explicitly disabled | SQLite single-writer + local-disk audio store — see `STAGING_READINESS.md` |
| Disk | Persistent mount at `/home` (App Service default), sized for DB + audio cache + HF model cache (the Indic Parler weights are several GB on disk once downloaded) — **20–30 GB** is comfortably sufficient for a pilot, well inside your stated 30 GB budget | `models/` cache is currently ~526 MB locally for the ONNX packs; the HF hub cache (including Indic Parler weights) was ~6.5 GB locally this session |
| Python version | 3.12 (Dockerfile base image `python:3.12-slim`) | `Dockerfile` line 7 |
| GPU | **Not required** | Confirmed CPU-only torch build used and working (§2) |

---

## 6. Docker — line-by-line audit

The `Dockerfile` was reviewed line by line. Docker itself is not installed in this
environment, so **build/run/container-HTTP/container-voice-E2E are NOT VERIFIED FROM
REPOSITORY** — reported honestly, not assumed. What the static review found and fixed:

**A real bug, found and fixed this session**: the two `pip install` lines installing
`torch`/`transformers`/`parler_tts` used unquoted `>=` version specifiers inside a Dockerfile
`RUN` (shell form). `/bin/sh` parses `torch>=2.3` as *output redirection* (`torch` with stdout
redirected to a file literally named `=2.3`), not as a version constraint — confirmed by direct
reproduction of the exact same shell parsing behavior this session. This means the version
constraints were **silently ignored entirely** on every previous build; pip installed whatever
the latest available version was, and the image accumulated stray junk files. Fixed by quoting
each specifier and pinning to the exact versions verified working this session (`torch==2.14.0`,
`transformers==4.46.1`, `parler_tts==0.2.3`) — see `CHANGELOG.md` and the commit for this pass.

**Everything else reviewed and found correct**:
- Base image `python:3.12-slim` — matches the Python version this repository targets.
- `libsndfile1` installed via `apt-get` — the actual runtime dependency `soundfile` needs;
  confirmed present in `requirements.txt`.
- `.dockerignore` correctly excludes `.env`/`.env.*` (with `.env.example` explicitly
  re-included), `runtime/`, `models/`, `.cache/`, `*.db*`, `*.wav`, `.git`, `.claude` — no
  secrets, local database, cached models, or working scratch state would be baked into the
  image.
- `ENV PYTHONUNBUFFERED=1` — correct for container log streaming.
- `EXPOSE 8000` / `CMD ... --port ${PORT:-8000} --workers 1` — matches §7's startup command
  exactly, no Windows-specific paths anywhere in the file.
- No `.env` is copied — `COPY . .` runs after `.dockerignore` exclusions apply.

---

## 7. Startup command

Exact, from the (now-fixed) `Dockerfile` — not invented:

```
uvicorn smriti_voice.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
```

- Module: `smriti_voice.api.app`, ASGI object `app`.
- `--workers 1` is hardcoded deliberately (§5) — do not override via `WEB_CONCURRENCY` or
  `UVICORN_WORKERS`; the app's own startup check
  (`smriti_voice/api/app.py::warn_if_multi_worker_env_detected`) logs an error if either is
  detected set above 1, though it cannot force-correct a misconfigured launch command itself.

---

## 8. Environment variables — exact configuration matrix

No secret values below — names, requirement, and classification only. Extracted directly
from `smriti_voice/config.py` and `smriti_voice/api/dependencies.py` (every `_env_str`/
`_env_bool`/`_env_int`/`_env_float`/`os.getenv` call in the codebase).

| Variable | Required | Secret? | Default | Azure location | Notes |
|---|---|---|---|---|---|
| `SMRITI_API_KEY` | Yes (single-patient mode) | **Secret** | none | App Settings | Generate via `python -m smriti_voice.api.generate_key` |
| `SMRITI_API_KEYS` | Alt. (multi-patient mode) | **Secret** | none | App Settings | JSON map; use for the real Backend gateway (§10 of `docs/VOICEBOT_INTEGRATION_GUIDE.md`) |
| `SMRITI_AUTH_USER_ID` | With `SMRITI_API_KEY` | Safe (identifier) | none | App Settings | |
| `SMRITI_ENV` | Recommended | Safe | `production` | App Settings | Fails closed if unset/misspelled — confirmed in `config.py` |
| `SMRITI_ALLOW_UNAUTHENTICATED` | No — never set on Azure | Safe (but dangerous) | `false` | — | Only for local dev; logs a warning if enabled |
| `GROQ_API_KEY` | Yes | **Secret** | none | App Settings | Verified real this session |
| `GROQ_MODEL` | Recommended | Safe | `qwen/qwen3.8-27b` | App Settings | Matches the verified-working model |
| `SARVAM_API_KEY` | Yes | **Secret** | none | App Settings | Verified real this session |
| `SARVAM_CHAT_MODEL` | Optional | Safe | `sarvam-105b` | App Settings | |
| `SARVAM_TTS_MODEL` | Optional | Safe | `bulbul:v3` | App Settings | Already fixed from a deprecated model id in a prior pass — do not regress |
| `SMRITI_LLM_PROVIDER` | Recommended | Safe | `auto` | App Settings | `groq`, matching validated config |
| `SMRITI_TTS_PROVIDER` | Recommended | Safe | `auto` | App Settings | `auto` correctly tries `local`/`sarvam`/`indic_parler` in order |
| `SMRITI_ASR_PROVIDER` | Optional | Safe | `auto` | App Settings | |
| `HF_TOKEN` | Yes (Indic Parler is being kept enabled) | **Secret** | none | App Settings | Required for the gated `ai4bharat/indic-parler-tts` model |
| `SMRITI_INDIC_PARLER_ENABLED` | Yes (per §4's decision to preserve it) | Safe | `false` | App Settings | Set `1` |
| `SMRITI_INDIC_PARLER_LANGUAGES` | Recommended | Safe | `asm,brx,mni,npi` | App Settings | Matches current working config |
| `SMRITI_INDIC_PARLER_DEVICE` | Recommended | Safe | `cpu` | App Settings | No GPU on this tier — keep `cpu` |
| `SMRITI_INDIC_PARLER_MODEL` | Optional | Safe | `ai4bharat/indic-parler-tts` | App Settings | |
| `SMRITI_INDIC_PARLER_DESCRIPTION` | Optional | Safe | a calm, neutral voice description | App Settings | |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` | Optional | **Secret** | none | App Settings | Only if that fallback provider is wanted |
| `SMRITI_DB_PATH` | Yes | Safe (a path) | `<repo>/runtime/smriti.db` | App Settings | **Must** point into the persistent mount, e.g. `/home/smriti-data/smriti.db` — the default is not persistent on Azure |
| `SMRITI_AUDIO_CACHE_DIR` | Yes | Safe | `<repo>/runtime/audio` | App Settings | Same — e.g. `/home/smriti-data/audio` |
| `HF_HOME` | Recommended (Indic Parler enabled) | Safe | system default | App Settings | e.g. `/home/smriti-data/hf-cache`, avoids re-downloading ~GB of weights on every restart |
| `SMRITI_RATE_LIMIT_PER_MINUTE` | Optional | Safe | `60` | App Settings | Per-key |
| `SMRITI_SESSION_IDLE_MINUTES` | Optional | Safe | `30` | App Settings | |
| `SMRITI_MAX_HISTORY_TURNS` | Optional | Safe | repo default | App Settings | |
| `SMRITI_AUDIO_RETENTION_MINUTES` | Optional | Safe | ~15 | App Settings | |
| `SMRITI_VOICE_JOB_QUEUE_MAX` | Optional | Safe | repo default | App Settings | |
| `SMRITI_VOICE_JOB_PROCESSING_DEADLINE_S` | Optional | Safe | repo default | App Settings | |
| `SMRITI_IDEMPOTENCY_TTL_HOURS` | Optional | Safe | repo default | App Settings | |
| `SMRITI_MAX_WAV_DURATION_S` | Optional | Safe | repo default | App Settings | |
| `SMRITI_MAX_RETRIES` / `SMRITI_RETRY_BACKOFF_S` | Optional | Safe | repo default | App Settings | Provider call retry tuning |
| `SMRITI_REQUEST_TIMEOUT_S` / `SMRITI_LLM_TIMEOUT_S` / `SMRITI_TTS_TIMEOUT_S` | Optional | Safe | repo defaults | App Settings | |
| `SMRITI_WEATHER_PROVIDER` / `SMRITI_DEFAULT_LOCATION` / `SMRITI_DEFAULT_LATITUDE` / `SMRITI_DEFAULT_LONGITUDE` | Optional | Safe | repo defaults | App Settings | Only relevant if the weather tool is used |
| `SMRITI_FORCE_OFFLINE` | No — do not set on Azure | Safe | `false` | — | Testing-only override |
| `SMRITI_LOCAL_LLM_PATH` | No — not applicable on Azure | Safe | unset | — | Points at a local model server; not used in this deployment |
| `WEBSITES_PORT` | **Yes — Azure-specific** | Safe | none | App Settings | Must be `8000` to match the container's actual listening port |
| `WEB_CONCURRENCY` / `UVICORN_WORKERS` | **Do not set** | — | — | — | The app itself warns if either is detected `>1` |

**Variables referenced in `.env.example`/`.env.staging.example` but confirmed unused in
current code**: none found this session — a full grep of every `_env_str`/`_env_bool`/
`_env_int`/`_env_float`/`os.getenv` call was cross-checked against both files; no drift found.

---

## 9. Docker validation status (exact test levels, not conflated)

| Test level | Status | What it would prove | What it does NOT prove |
|---|---|---|---|
| Unit tested | ✅ Done (`pytest -q`, 593+ passing) | Application logic is correct | Nothing about Docker or Azure |
| Integration tested | ✅ Done (same suite, `TestClient`) | HTTP contract is correct in-process | Nothing about a real network, container, or Azure |
| Real provider tested | ✅ Done this session (§2) | Sarvam/Groq/Indic Parler actually work with real credentials | Nothing about Docker or Azure specifically — this ran directly on the host |
| Real HTTP tested | ✅ Done this session and prior (standalone `uvicorn`, real requests) | The FastAPI app serves correctly over a real socket | Still not inside a container |
| Real Docker container tested | ❌ **NOT VERIFIED FROM REPOSITORY** | The exact deployment artifact builds and runs correctly | Docker is not installed in this environment; this must be done before or during actual Azure deployment |

**Do not treat any of the first four as proof the Docker image works** — they are genuinely
different things, and the gap between "works on this Windows host" and "works in the Linux
container Azure will actually run" is exactly what a real `docker build && docker run` closes.
This is the single most important remaining verification step before deployment.

---

## 10. Persistent storage — exact paths

- **SQLite** and **audio cache** must both be redirected via `SMRITI_DB_PATH` and
  `SMRITI_AUDIO_CACHE_DIR` (§8) to somewhere under Azure App Service's persistent `/home`
  mount — e.g. `/home/smriti-data/smriti.db` and `/home/smriti-data/audio`. The code already
  supports arbitrary absolute paths for both (confirmed: `Database.__init__` creates the
  parent directory if missing; no hardcoded-relative-path assumption exists beyond the
  *default* value).
- **Confirmed still writing to relative/container-local paths only in their defaults**, never
  hardcoded elsewhere: `SMRITI_DB_PATH` defaults to `<repo>/runtime/smriti.db`,
  `SMRITI_AUDIO_CACHE_DIR` defaults to `<repo>/runtime/audio` — both inside the app's own
  working directory, which is **not** persistent on Azure App Service for Containers unless
  explicitly redirected. This is a configuration requirement (§8), not a code defect.
- **HF model cache**: redirect via `HF_HOME` for the same reason — otherwise Indic Parler-TTS
  weights re-download on every restart (several GB, several minutes, unnecessary Azure
  bandwidth cost).
- Database migrations (`PRAGMA user_version`-based) and voice-job restart recovery
  (`recover_stale_jobs()`) both run automatically at startup and require no manual step — but
  both are only meaningful if the underlying SQLite file actually persisted across the
  restart (i.e., only if the above paths are correctly redirected).

---

## 11. Audio storage

- `AudioStore` (`smriti_voice/tts/router.py`) writes to local disk under
  `SMRITI_AUDIO_CACHE_DIR`, prunes files older than `SMRITI_AUDIO_RETENTION_MINUTES` on every
  write, and additionally re-checks the cutoff at read time (`path_for`) so a stale file
  can't be served even if no new write has triggered a prune yet.
- **Path traversal protection**: `path_for` explicitly validates `audio_id` is hex-only
  before ever constructing a filesystem path from it — confirmed by reading the code this
  session (`smriti_voice/tts/router.py::AudioStore.path_for`).
- **Ownership/patient isolation**: `GET /v1/audio/{audio_id}` resolves ownership through the
  voice-job record, not the filesystem — cross-patient access is `404`, verified by existing
  tests (`test_e2e_contract_harness.py::test_cross_patient_isolation_across_the_full_chain`).
- **Restart behavior**: files on the persistent mount survive a restart; the short retention
  window means most audio ages out on its own regardless.
- **Blob Storage**: not necessary now — see the reasoning already in
  `STAGING_READINESS.md`'s Azure section (short-lived, disposable, single-instance-appropriate
  local cache). Revisit only if the deployment ever needs multiple instances.

---

## 12. Authentication — exact behavior

- `x-api-key` header, validated server-side in `api/dependencies.py::require_api_key`.
- Missing/invalid key → `401`.
- Valid key, `user_id` not in that key's authorized set → `403`.
- Valid key, authorized `user_id` → proceeds normally.
- Verified this behavior applies uniformly across `/v1/conversation`,
  `/v1/conversation/voice`, `/v1/conversation/welcome`, `/v1/memory/sync`,
  `/v1/voice/jobs/*`, `/v1/audio/*` — every one of these routers carries
  `dependencies=[Depends(require_api_key)]` at the router level (confirmed by direct
  inspection this session — no route was found missing this dependency).
- **Patient identity cannot be spoofed**: `authorized_user_ids`/`authenticated_user_id` are
  derived entirely from the server-side `SMRITI_API_KEYS`/`SMRITI_API_KEY` configuration, never
  from anything the request body claims — a `user_id` outside what the key authorizes is
  always `403`, regardless of what the caller sends.
- **Multi-patient readiness**: `SMRITI_API_KEYS` already supports `{"key": ["patient-1",
  "patient-2", ...]}` — one Backend gateway credential authorized for many patients is
  already implemented and tested (`tests/integration/test_multi_user_auth.py`). No VoiceBot
  code changes are needed for the Backend integration.

---

## 13. Swagger / OpenAPI — fixed this session

**Found and fixed a real gap**: the generated OpenAPI schema previously had no
`securitySchemes` and no per-operation `security` requirement, because `require_api_key` is a
plain `Header()`-based `Depends()`, which FastAPI does not automatically recognize as
authentication. This meant Swagger UI showed no "Authorize" button and incorrectly implied
`x-api-key` was optional on every protected route.

**Fix** (`smriti_voice/api/app.py`): a custom `openapi()` override that adds an `ApiKeyAuth`
security scheme (`type: apiKey, in: header, name: x-api-key`) and applies it to every
operation except the `health`/`languages` tags — which genuinely require no authentication.
**This does not change the authentication mechanism or how it's enforced** — only what the
generated document describes. Verified: `tests/unit/test_openapi_security.py` (4 tests,
including a check that every protected path declares the requirement and every public path
doesn't), plus a real HTTP check that `/docs`, `/openapi.json`, and `/redoc` are all
reachable without a credential (necessary — Swagger has to be browsable before you can
discover how to authenticate).

- `GET /docs` — reachable, unauthenticated, usable for manual testing after deployment.
- `GET /openapi.json` — reachable, unauthenticated, now correctly describes `x-api-key`.
- `GET /redoc` — reachable, unauthenticated.

---

## 14. Health

`GET /v1/health` (`smriti_voice/api/routes/health.py`, docstring: *"Never returns a
credential"*) — unauthenticated by design, safe for a public health-check probe. Returns
liveness plus boolean capability flags (which providers are *configured*, not their values) —
confirmed no credential values, patient data, or memory content appear anywhere in the
response body. Suitable directly as the Azure health-check path.

No separate readiness endpoint exists or is needed for this pilot — `/v1/health` already
distinguishes "process alive" from "critical config present" via its boolean fields.

---

## 15. Logging / monitoring

- Confirmed (source review this session, consistent with prior sessions'
  `tests/unit/test_privacy_data_minimization.py`): logs never contain API keys, `x-api-key`
  values, `Authorization` headers, raw phone numbers, raw audio, full transcripts, full
  memory payloads, signed URLs, or bearer credentials — redaction (`smriti_voice/logging.py`)
  applies uniformly.
- **Minimum recommended Azure monitoring for the pilot**: Application Insights, basic tier,
  wired to the App Service (a few clicks in the Portal, no code change required — App
  Service's native Application Insights extension instruments the process automatically).
  Do not over-engineer this — no separate log-aggregation pipeline, no custom metrics
  exporter, is justified for a pilot of this scale.

---

## 16. Backup / restore

Unchanged from `STAGING_READINESS.md`'s existing storage plan: `tools/backup_db.py backup`
(SQLite online-backup API, safe against a live database) run on a schedule against the
`/home`-mounted DB; copy the resulting file off-instance (a small Storage Account container
is the simplest option) before any real patient data exists. If the App Service is destroyed,
everything under `/home` — including the SQLite file and audio cache — is destroyed with it,
unless backups were copied off-instance beforehand.

---

## 17. Rollback

App Service's GitHub-integrated deployment history lets you redeploy a previous successful
build without touching `/home` — the safest rollback path, since it never touches persisted
data directly. Migrations in this codebase are additive/forward-only; rolling back to a code
version *older* than the currently-applied schema version is not automatically guarded
against — take a `tools/backup_db.py backup` snapshot before any rollback regardless.

---

## 18. Provider connectivity

Plain outbound HTTPS to Sarvam's and Groq's API endpoints via `httpx` — no VPN, private
endpoint, or provider-specific Azure networking is required. Azure App Service permits
outbound HTTPS by default.

---

## 19. Cost considerations

With Indic Parler-TTS **kept enabled** (this task's explicit instruction), the compute tier
requirement is meaningfully higher than a "text + Sarvam-only" pilot would need:

| Resource | Tier | Approx. monthly cost |
|---|---|---|
| App Service Plan (Linux, Premium v3 P1v3-class: 2 vCPU / 8 GB) | Premium v3 | Materially higher than Basic — check current Azure Student pricing at deployment time; budget for this being the dominant cost line |
| Persistent storage (`/home` mount) | Included in the Plan | $0 extra |
| Application Insights (basic) | Pay-as-you-go, low volume | ~$0–$5/mo at pilot traffic |
| Outbound bandwidth | Metered, small payloads | Negligible at pilot scale |
| HTTPS / `*.azurewebsites.net` | Included | $0 |

**This is a real, deliberate tradeoff you are making by keeping Indic Parler-TTS enabled** —
report the exact current Premium v3 P1v3 price from the Azure Portal against your Student
credit balance before committing, since pricing changes over time and by region.

---

## 20. Troubleshooting (anticipated, not yet encountered — no Azure deployment exists)

- **`ZoneInfoNotFoundError` on startup**: `tzdata` must be present — already exact-pinned in
  `requirements.txt` (fixed in a prior pass; confirmed still present this session).
- **Empty conversation history after a restart**: `SMRITI_DB_PATH` is not pointed at the
  persistent mount — check §8/§10 first.
- **`TTS_UNAVAILABLE` only for asm/brx/mni/npi, `eng`/`hin` work fine**: expected if
  `SMRITI_INDIC_PARLER_ENABLED` is unset or `HF_TOKEN` is missing/invalid — those four
  languages have no other TTS path.
- **Slow first voice-output request for asm/brx/mni/npi**: expected — Indic Parler-TTS loads
  lazily on first use (~36–39s measured this session), not a hang.
- **`401` on every request despite a correct-looking key**: check for accidental whitespace
  or the wrong variable name (`SMRITI_API_KEY` vs `SMRITI_API_KEYS` — mutually exclusive
  modes, see §12) in App Settings.

---

## 21. Backend handoff

Once deployed:

```
VOICEBOT_BASE_URL=https://<app-name>.azurewebsites.net
VOICEBOT_API_KEY=<generated via python -m smriti_voice.api.generate_key, set as SMRITI_API_KEY
                  or as one entry in SMRITI_API_KEYS>
```

Hand these to the Backend developer through a secure, out-of-band channel — never through
GitHub, chat history, or any file in this repository. Accompany them with
`docs/BACKEND_VOICEBOT_INTEGRATION.md`, `docs/VOICEBOT_INTEGRATION_GUIDE.md`, and
`docs/integration/openapi.json`. **Flutter receives none of this** — see
`docs/FLUTTER_VOICEBOT_INTEGRATION.md` §1–2, unchanged and still accurate.

---

## 22. What this document does not claim

No Azure resource has been created. No deployment has occurred. No Docker build has been run
(Docker is not installed in this environment). This document is the deployment plan and
configuration reference to execute against — not a record of a completed deployment.
