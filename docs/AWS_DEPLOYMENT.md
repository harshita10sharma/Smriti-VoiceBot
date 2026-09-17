# AWS Deployment — SMRITI VoiceBot

**Status: the authoritative, current deployment reference.** Azure was evaluated in an
earlier pass (`AZURE_DEPLOYMENT.md`, now historical) — this document supersedes it. The
technical findings that don't depend on the cloud provider (real provider validation, the
measured Indic Parler-TTS memory footprint, the Dockerfile bug found and fixed) are carried
over and re-cited here, not re-derived.

**This document is a deployment PLAN plus ready-to-run tooling (`deployment/aws/`), not a
record of a completed deployment.** No AWS resource has been created. AWS CLI/credentials
are not configured anywhere in this development environment — no `aws` binary, no
`~/.aws/credentials`, no `AWS_*` environment variables, no `boto3`. This is the one external
blocker preventing Phases 16–26 of the release task from actually executing; everything
achievable without AWS access is complete.

---

## 1. Non-negotiable principle

The working provider stack (Groq LLM, Sarvam ASR, Sarvam TTS, Indic Parler-TTS) is
preserved exactly as-is. Nothing was disabled or downgraded to reduce AWS cost. Where a
component needs a larger instance, that requirement is stated plainly (§4) rather than
worked around by removing functionality.

## 2. Recommended AWS architecture

```
                     HTTPS (443)
                         |
                 [ Elastic IP / DNS ]
                         |
        ┌────────────────────────────────┐
        │   ONE EC2 instance              │
        │   ONE Docker container          │
        │   ONE Uvicorn worker            │
        │                                 │
        │   FastAPI app (this repo)       │
        │                                 │
        │   /data  (EBS volume, mounted)  │
        │     ├── smriti.db (SQLite)      │
        │     ├── audio/ (TTS cache)      │
        │     └── hf-cache/ (model cache) │
        └────────────────┬────────────────┘
                          │ outbound HTTPS only
                          ▼
              Sarvam API   /   Groq API
```

**One EC2 instance. One container. One Uvicorn worker. No Kubernetes, no ECS, no Lambda,
no RDS, no autoscaling, no load balancer.** This matches the task's explicit instruction not
to introduce infrastructure the current architecture doesn't measurably need, and matches
this repository's own existing constraints:

- **SQLite is a single-writer database.** Two processes/instances writing the same file
  concurrently is a real corruption risk, not a theoretical one — confirmed by this
  repository's own architecture docs and the in-process (not distributed) locking added in
  earlier work this session.
- **`AudioStore` writes to local disk.** A second instance would not be able to serve audio
  a first instance generated — there is no shared/replicated storage layer for it.
- **Indic Parler-TTS is a large, process-local, lazily-loaded model** (confirmed: ~3.49 GB
  RSS once loaded, §4). A second worker process would load a second full copy into memory.
  The `Dockerfile` already hardcodes `--workers 1` for exactly this reason.

If the pilot ever needs to scale beyond one instance, that requires a genuine architecture
change (a shared database, a shared object store for audio, a distributed lock) — not an
EC2/ECS configuration change. Nothing here proposes that; it isn't needed for a pilot.

## 3. Why EC2, not ECS/Fargate/Lambda

- **Not Lambda**: this is a long-running process with in-memory state (the rate limiter and
  per-session locks added earlier this session) and a background voice-job worker thread —
  fundamentally the wrong execution model for Lambda's request-scoped, ephemeral containers.
- **Not ECS/Fargate**: adds real operational complexity (task definitions, a container
  registry, a service, often a load balancer) for no benefit over a single EC2 instance at
  this scale, and Fargate's ephemeral storage model would need explicit EFS wiring to match
  what an EC2 instance's own attached EBS volume gives for free.
- **EC2 + an attached EBS volume** mirrors exactly the pattern already validated for the
  earlier Render deployment (`render.yaml`'s `/data` disk mount) and the Azure analysis
  (`AZURE_DEPLOYMENT.md`'s `/home` persistent mount) — same shape, different platform,
  genuinely the simplest architecture that satisfies the single-writer/local-audio/
  process-local-model constraints above.

## 4. Real provider validation and measured resource requirements

Carried over from this session's real validation (re-cited, not re-run, since neither the
provider integration code nor the environment changed since it was measured):

| Component | Provider | Model | Result | Evidence |
|---|---|---|---|---|
| LLM | Groq | `qwen/qwen3.8-27b` | **WORKING** | Real call, 814ms latency, real generated text |
| ASR | Sarvam | Saaras family | **WORKING** | Real call against real synthesized speech audio, 845ms latency, real transcript |
| TTS | Sarvam | `bulbul:v3` | **WORKING** | Real call, real audio bytes returned |
| TTS (local) | Indic Parler-TTS | `ai4bharat/indic-parler-tts` | **WORKING** | Real call for Assamese, model loaded from local HF cache in ~36–39s |

**Measured memory footprint** (this session, one language loaded, one synthesis performed):
baseline ~56 MB RSS before any model load → **~3.49 GB RSS** after Indic Parler-TTS loads
and runs once. This is a real measurement, not the Dockerfile comment's earlier unverified
"~8 GB" note.

**Recommended EC2 instance, accounting for this measurement**: **`m6i.xlarge`** (4 vCPU,
16 GB RAM) or, for a tighter budget, **`m6i.large`** (2 vCPU, 8 GB RAM) — either gives
comfortable headroom above the measured 3.49 GB baseline for OS overhead, the container
runtime, and concurrent request buffers. Do **not** use a `t3.micro`/`t3.small`-class
instance (1–2 GB RAM) if Indic Parler-TTS stays enabled — that leaves negative or
near-zero headroom above the measured baseline. If Indic Parler-TTS is ever disabled for a
lighter deployment (a decision this document does not make for you — see §5), a
`t3.small`/`t3.medium` becomes viable, since the remaining components (FastAPI, SQLite, the
Sarvam/Groq HTTP clients) have a small footprint by comparison.

**Docker/container validation status**: **NOT VERIFIED FROM REPOSITORY.** Docker is not
installed in this development environment (`docker: command not found`), so `docker build`/
`docker run` could not be executed here. A full line-by-line Dockerfile review was performed
instead, which found and fixed a real, previously-unnoticed bug: the `torch`/`transformers`/
`parler_tts` version specifiers were unquoted in a shell `RUN` command, so `/bin/sh` parsed
`torch>=2.3` as output redirection rather than a version constraint — confirmed by direct
reproduction of the exact parsing behavior. Fixed (quoted, exact-pinned to the versions
verified working this session: `torch==2.14.0`, `transformers==4.46.1`,
`parler_tts==0.2.3`). **The actual `docker build`/`docker run`/container HTTP/container
voice-E2E tests remain outstanding** and must be run once Docker or AWS access is available
— do not treat the static review as equivalent to running the container.

## 5. Indic Parler-TTS: preserved, cost tradeoff stated explicitly

Currently enabled (`SMRITI_INDIC_PARLER_ENABLED=1` locally), covering `asm,brx,mni,npi` —
languages with **no Sarvam Bulbul TTS coverage at all** (confirmed:
`docs/integration/language_matrix.json` shows `tts_online: false` for all four via Sarvam).
This is not a redundant fallback; for these four languages it is the *only* real TTS path
that exists. It has not been disabled to reduce AWS cost, per this task's explicit
instruction. The real cost implication (a `m6i.large`/`m6i.xlarge`-class instance instead of
a `t3.small`) is stated in §4 and §9 rather than hidden.

## 6. Required environment variables

Same variable set as the Azure analysis (the application doesn't care which cloud it runs
on) — see `AZURE_DEPLOYMENT.md` §8 for the full table with requirement/secret
classification. AWS-specific differences:

| Variable | Azure equivalent | AWS value |
|---|---|---|
| `SMRITI_DB_PATH` | `/home/smriti-data/smriti.db` | `/data/smriti.db` (the EBS mount point) |
| `SMRITI_AUDIO_CACHE_DIR` | `/home/smriti-data/audio` | `/data/audio` |
| `HF_HOME` | `/home/smriti-data/hf-cache` | `/data/hf-cache` |
| `WEBSITES_PORT` (Azure-only) | `8000` | not applicable — EC2 has no equivalent platform-routing setting; the security group (§8) controls what's reachable |

All secrets (`SMRITI_API_KEY`/`SMRITI_API_KEYS`, `GROQ_API_KEY`, `SARVAM_API_KEY`,
`HF_TOKEN`) go in an environment file on the instance (or AWS Systems Manager Parameter
Store, for a slightly more auditable pilot setup — not required to launch), never in the
Docker image, never committed to Git.

## 7. Persistence

`SMRITI_DB_PATH`, `SMRITI_AUDIO_CACHE_DIR`, and `HF_HOME` (if Indic Parler stays enabled)
must all point into a directory backed by an **attached, non-root EBS volume** (e.g.
mounted at `/data`), not the instance's ephemeral root volume alone. An EBS volume survives
an instance stop/start and a container restart; it does not automatically survive instance
*termination* unless it's explicitly detached/reattached or snapshotted first (§10).

**Required persistence test sequence** (to run once an instance exists):
1. Write data (sync a memory snapshot, run a conversation turn).
2. Restart the container (`docker restart` or equivalent) — read the data back.
3. Restart/reboot the EC2 instance itself — read the data back again.
4. Confirm `recover_stale_jobs()` correctly marks any job that was mid-flight before the
   restart as `failed`/`INTERRUPTED_BY_RESTART` (already implemented and tested locally —
   `tests/integration/test_voice_job_cancellation_and_deadline.py`; this step re-confirms
   it against the real persisted database, not just an in-process test).

## 8. Security

- **Security group**: inbound `443` (HTTPS) from `0.0.0.0/0` (or restricted further once the
  Backend's own IP range is known); inbound `22` (SSH) restricted to a specific known IP,
  never open to the world; no other inbound ports. The application's internal port (8000)
  is never exposed directly — only the TLS-terminating reverse proxy (§11) listens
  externally.
- **SSH key pair**: a dedicated key pair for this instance, private key never committed,
  never shared over an insecure channel.
- **IAM**: the EC2 instance role should have only what it actually needs — for this
  architecture, that's nothing beyond default (no S3/DynamoDB/other AWS API access is
  required for the application itself). Only add an IAM permission (e.g. S3 write, for
  backups — §10) when actually wiring that feature, not preemptively.
- **No public database**: SQLite isn't a network service at all, so there's nothing to
  expose — the file is only ever reachable by the process that opened it, on that one
  instance.
- **Secrets**: environment file on the instance, permissions restricted to the running
  user; never in the Docker image (the existing `.dockerignore` already excludes `.env`);
  never committed.

## 9. Cost estimate (AWS, general guidance — verify current pricing in the AWS console
before committing spend)

**NOT VERIFIED FROM REPOSITORY / live pricing API** — this uses general, well-known
approximate on-demand pricing for the `ap-south-1` (Mumbai) region as general knowledge, not
a fetched live quote. Confirm exact current pricing in the AWS Pricing Calculator or console
before relying on this for a budget decision.

| Resource | Approx. spec | Rough on-demand cost |
|---|---|---|
| EC2 `m6i.large` (2 vCPU/8 GB, Indic Parler enabled) | On-demand, `ap-south-1` | Roughly $0.10–$0.12/hour → ~$75–$90/month if run continuously |
| EC2 `m6i.xlarge` (4 vCPU/16 GB, more headroom) | On-demand | Roughly $0.20–$0.24/hour → ~$150–$175/month |
| EC2 `t3.small` (2 vCPU/2 GB, Indic Parler **disabled**) | On-demand | Roughly $0.02–$0.03/hour → ~$15–$20/month |
| EBS volume (30 GB, gp3) | | A few dollars/month |
| Data transfer out | Pilot-scale traffic | Likely negligible at this scale |
| Elastic IP (if the instance isn't always running) | | Small hourly charge only while unattached |

A **Reserved Instance or Savings Plan** would reduce the on-demand hourly rate materially
for a pilot expected to run continuously for months — worth considering once the instance
type is finalized, not before. **What happens when AWS credits expire**: the instance keeps
running and billing normally against the account's payment method — set a billing alert in
AWS Budgets before that point, since nothing in this architecture auto-shuts-down on credit
exhaustion.

## 10. Backup / restore

- **Backup**: `tools/backup_db.py backup` (already exists in this repository, uses SQLite's
  online-backup API, safe against a live database) run on a schedule (e.g. a cron job on the
  instance) against the `/data`-mounted DB.
- **Destination**: for the pilot, copying the backup file to an S3 bucket is acceptable and
  simple — this requires the EC2 instance role to have write access to that one bucket
  (least-privilege IAM policy, not broad S3 access) and the `boto3`/`aws s3 cp` call to run
  as part of the backup script; this is not wired into the application runtime itself, only
  the backup job.
- **Restore**: `tools/backup_db.py restore` (already exists, preserves the pre-restore file
  as `.pre-restore`) — download the backup from S3 back onto the instance's `/data` volume,
  then run the restore command.
- **If the EC2 instance is terminated**: everything on its root volume is gone; the `/data`
  EBS volume survives only if explicitly preserved (not set to delete-on-termination) or if
  backups were copied off-instance to S3 beforehand. Recommend: disable delete-on-termination
  for the `/data` volume AND maintain off-instance S3 backups — belt and suspenders for a
  pilot with real (even if limited) patient data.

## 11. HTTPS

The Backend must receive `VOICEBOT_BASE_URL=https://...`, never a bare HTTP/IP URL. Simplest
secure option for one instance: a domain name (even a subdomain you control) pointed at the
instance's Elastic IP, with a reverse proxy (Caddy or nginx + Certbot) on the instance
terminating TLS via Let's Encrypt and proxying to the container's internal port. This avoids
introducing an Application Load Balancer (a real cost and complexity addition for a single
instance with no need for load distribution) while still giving a real, stable HTTPS URL.
`deployment/aws/first_boot_setup.sh` installs and configures Caddy for exactly this (`reverse_proxy 127.0.0.1:8000`, automatic certificate) — the domain must already resolve to the
instance's Elastic IP before running it, or Let's Encrypt's HTTP-01 challenge fails.

## 12. Deployment method

Same reasoning as the Azure analysis: build the image from this repository's `Dockerfile`
(now with the pinning bug fixed) via a CI step or directly on the instance, push to **ECR**
if a registry is wanted for reproducibility/versioning (recommended for a pilot that expects
more than one deployment), or build directly on the instance for the simplest possible first
deployment. `deployment/aws/` (§14) provides scripts for both paths.

## 13. Startup command

Unchanged from the Dockerfile (verified, not invented):
```
uvicorn smriti_voice.api.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
```

## 14. `deployment/aws/` — what's provided

See the files in that directory for the actual reproducible tooling: environment template,
provisioning script (EC2 + security group + EBS, via AWS CLI — chosen over a heavier IaC
tool like Terraform/CloudFormation for a single-instance pilot, per the "do not introduce
unnecessary infrastructure" instruction; note as IaC if the deployment grows), deployment
script, smoke-test script, and rollback notes. **None of these have been executed** — they
are ready to run once AWS credentials are configured in this environment (§15).

## 15. Current live deployment (verified)

This plan has been executed. As of this writing:

| Item | Value |
|---|---|
| Base URL | `https://15-206-144-216.nip.io` |
| Instance | `m7i-flex.large` (2 vCPU / 8 GiB), `ap-south-1a` |
| Instance ID | `i-02191d418176eca9e` |
| EBS | 30 GB gp3 at `/data`, `DeleteOnTermination: false` |
| HTTPS | Caddy, real Let's Encrypt certificate, automatic renewal |
| Domain | `15-206-144-216.nip.io` (free wildcard DNS to the Elastic IP — no domain purchased) |
| Image | `smriti-voicebot:c37f211` (git short hash, matches `HEAD`) |

**Verified live, with real evidence, not merely deployed**: authenticated text conversation,
welcome, deterministic safety refusal, cross-patient/missing/invalid-auth rejection (403/401),
memory sync (including stale-revision rejection, conflicting-revision rejection, and
idempotent no-op replay), real Groq/Qwen calls, real Sarvam ASR/TTS, real Indic Parler-TTS
synthesis (asm), full async voice-job lifecycle including cancellation, audio retrieval, and
persistence across both a container restart and a full instance reboot (`/data`, Docker,
Caddy, and the container itself all survived and auto-recovered).

`m7i-flex.large` was chosen over the originally planned `m6i.large`/`m6i.xlarge` because the
AWS account rejected `m6i.large` outright as not Free-Tier eligible; `m7i-flex.large` was
verified (via `DescribeInstanceTypes`, not assumed) to have the same 2 vCPU/8 GiB shape and
to be Free-Tier eligible on this account. Its CPU is burstable/credit-based rather than
dedicated -- a real, measured consequence: Indic Parler-TTS generation took 20-27s for short
utterances (see `SMRITI_TTS_TIMEOUT_S=60` in `env.template`, raised from the 30s default for
exactly this reason).

### Post-launch reliability tuning (2026-09-16)

A comprehensive live re-sweep (~30 checks: auth matrix, safety refusal, command routing,
multi-user provisioning/isolation, simultaneous-patient text, cross-patient session-swap
rejection, disable/re-enable independence, memory-sync revisioning, prompt-injection
resistance, real voice round trip, cancellation, language-endpoint honesty) passed cleanly.
The one real, actionable finding: **Groq's own single-call latency varies from ~300ms up to
~35s even with zero concurrency** — confirmed by isolating one non-retried call, and by
container logs showing `llm_provider_failed` → a graceful `llm_unavailable` fallback rather
than a hang. Because production runs `SMRITI_LLM_PROVIDER=groq` explicitly (no fallback
chain), a slow Groq call has nothing to fail over to. `SMRITI_MAX_RETRIES` had been left
unset (defaulting to `2`), giving a worst-case wall-clock of ~90s (3 attempts × the 30s
per-attempt timeout) before the SDK gives up. Set explicitly to `1` in
`deployment/aws/.env.production.local` and redeployed (container restart only, no rebuild
needed) — halves the worst case to ~60s without reducing single-attempt reliability. This
bounds the problem; it does not remove Groq's own latency variance, which is a property of
the provider's free/shared tier, not of this codebase. See `STAGING_READINESS.md` §OPERATIONS
for the corresponding status-table entry.

### Real defects found only by actually deploying (all fixed, commit `32dfa49`)
- `torchaudio` resolved to a CUDA-linked build from the default PyPI index (parler_tts/
  transformers pull it transitively), failing at import on this CPU-only image with
  `libcudart.so.13: cannot open shared object file`. Fixed by pinning it to the CPU wheel
  index in the Dockerfile, same version, different source.
- Caddy's own documented install path (`@caddy/caddy` on Copr) has no Amazon Linux 2023
  build target at all. Replaced with Caddy's official static-binary install in
  `first_boot_setup.sh`.
- An apostrophe inside a `${VAR:?message}` expansion broke bash's parser even nested inside
  an outer double-quoted string -- reproduced with `bash -n` before fixing.
- Git Bash / MSYS on Windows silently rewrites any CLI argument that looks like a POSIX path
  (starting with `/`) into a Windows path, corrupting two separate AWS CLI calls
  (`ssm get-parameter --name /aws/service/...` and `ec2 attach-volume --device /dev/xvdf`).
  Fixed with `MSYS2_ARG_CONV_EXCL`, scoped to just those arguments -- a no-op on native
  Linux/macOS bash.

To reproduce this deployment from a clean AWS account: `deployment/aws/provision.sh` →
`deployment/aws/first_boot_setup.sh` (on the instance) → build the image (locally if Docker
is available, or directly on the instance if not, as was necessary here) → `deployment/aws/
deploy.sh` or the equivalent manual `docker run` → `deployment/aws/smoke_test.sh`.

Once that succeeds, `deployment/aws/provision.sh`, `deployment/aws/first_boot_setup.sh`, and
`deployment/aws/deploy.sh` (§14) are ready to execute the plan in this document, in that
order.
