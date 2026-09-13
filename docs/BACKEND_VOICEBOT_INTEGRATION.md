# Backend ↔ VoiceBot Integration

**Audience:** the Backend developer building the gateway between Supabase and VoiceBot.
This is implementation-focused; for the full reference, see
[`VOICEBOT_INTEGRATION_GUIDE.md`](VOICEBOT_INTEGRATION_GUIDE.md) and
[`../INTEGRATION_CONTRACT.md`](../INTEGRATION_CONTRACT.md).

This gateway does not exist yet in any repository this team has access to (verified by
inspecting the actual `Abhayk777/SMRITI` client repository — no edge function, schema, or
credential handling for VoiceBot exists there today). This document is the exact
step-by-step for building it.

## 1. Authenticate your own app/device request

Unchanged from your existing Supabase Auth flow — this happens entirely before VoiceBot
enters the picture. VoiceBot has no opinion about how the Flutter device authenticates to
your Backend.

## 2. Resolve patient identity

You already have this: `patients.id` (a UUID) is your system of record. Use it as
VoiceBot's `user_id` directly — VoiceBot's `user_id` accepts any string matching
`[A-Za-z0-9\-_.]{1,64}`, and a UUID fits that with no translation layer.

```
user_id = patients.id  # verbatim, as a string
```

## 3. Verify authorization

Before proxying anything to VoiceBot, confirm your own caller is allowed to act as this
patient (your existing `patient_members`/RLS logic — VoiceBot has no visibility into this
layer and must not be asked to enforce it). Separately, VoiceBot enforces that your
server-side `x-api-key` is itself authorized for this `user_id` (via `SMRITI_API_KEYS`) —
two independent checks, both must pass.

## 4. Provision / re-enable VoiceBot identity when needed

No separate provisioning call exists or is needed. The first `POST /v1/memory/sync` or
first conversational turn for a new `user_id` auto-provisions it. To re-enable a
previously disabled patient, send `POST /v1/memory/sync` with `"active": true` — this
route stays reachable even while disabled, specifically for this purpose.

```jsonc
POST /v1/memory/sync
{"user_id": "<patients.id>", "active": true,
 "family_members": [...], "medicines": [...], "daily_routines": [...]}
```

## 5. Build the VoiceBot memory snapshot

Full schema: [`integration/voicebot-memory-schema.json`](integration/voicebot-memory-schema.json).
Pull from your `get_patient_content(p_patient_id)` RPC (or equivalent), then whitelist —
never forward the raw RPC response, which includes private escalation data VoiceBot must
never see:

| Your field | → | VoiceBot field |
|---|---|---|
| `elder_name` / `display_name` | → | `display_name` |
| `lang_code` | → | `language_code` (send as-is — `hi`/`as`/`en` are already understood, see §7) |
| `timezone` | → | `timezone` |
| `version` (your `content_version`) | → | `source_revision`, if you adopt revisioning |
| `people[].id` | → | `family_members[].external_id` |
| `people[].name` | → | `family_members[].name` |
| `people[].relationship` | → | `family_members[].relation` |
| `people[].memory_prompt` | → | `family_members[].memory_prompt` |
| `people[].is_deceased` | → | `family_members[].is_deceased` |
| `medications[].id` | → | `medicines[].external_id` |
| `medications[].name` | → | `medicines[].name` |
| `medications[].dose` | → | `medicines[].dose` |
| `medications[].chosen_time_min` / `window_start_min` / `window_end_min` | → | same field names, unchanged |
| `medications[].days_of_week` | → | `medicines[].days_of_week`, unchanged format |
| `medications[].active` | → | `medicines[].active` |
| `routine[].id` | → | `daily_routines[].external_id` |
| `routine[].time_min` | → | `daily_routines[].time` — **convert to `"HH:MM"`** (see §6) |
| `routine[].label_key` | → | `daily_routines[].activity` — **resolve to human-readable text first** (VoiceBot never sees your i18n keys) |

**Never forward**: `photo_path`, `voice_path` (storage paths — VoiceBot doesn't need
them), `escalation_config` (private, patient-level, unrelated to VoiceBot's contract),
`age`/`education_years` (VoiceBot doesn't ask for them unless a specific feature needs
them — it does not today).

## 6. Transform `routine_items.time_min` into VoiceBot's schedule representation

VoiceBot's routine `time` field is a `"HH:MM"` 24-hour string — different on purpose from
how medicines are represented (medicines have a window to validate; routines don't).
Convert on your side:

```python
def time_min_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
```

Do not ask VoiceBot to accept integer minutes for routines — this is documented,
intentional, and not a gap to route around.

## 7. Transform frontend language codes

You do not strictly need to transform anything — VoiceBot already normalizes `hi`, `as`,
`en` (and accepts `mni`/`kha`/`lus` as-is) at the API boundary before any downstream use.
Send whichever form your system already has. If you want to be explicit anyway:

| Your code | VoiceBot canonical |
|---|---|
| `hi` | `hin` |
| `as` | `asm` |
| `en` | `eng` |
| `mni` | `mni` (same) |
| `kha` | `kha` (same — but nothing is actually available for Khasi today, see §12 of the integration guide) |
| `lus` | `lus` (same — same caveat) |

## 8. Proxy text requests

```jsonc
POST /v1/conversation
Headers: x-api-key: <your VoiceBot credential>
         X-Idempotency-Key: <if you might resend this exact call>
{"user_id": "<patients.id>", "session_id": "<from Flutter, or null>",
 "message": "<transcribed or typed text>", "language": "<optional>"}
```
Return the response body to Flutter largely as-is; you decide how much of `metadata` your
client needs.

## 9. Proxy voice requests

```jsonc
POST /v1/conversation/voice   (multipart)
Headers: x-api-key: <your VoiceBot credential>
Fields: audio_wav (file), user_id, session_id (optional), language (optional),
        speak (default true)
```
Stream the multipart body through rather than buffering the whole file if your platform
supports it — VoiceBot enforces `SMRITI_MAX_UPLOAD_BYTES` (default 10 MiB) regardless.

## 10. Forward idempotency keys correctly

If Flutter or your own retry logic generates an `X-Idempotency-Key`, forward it verbatim
on the proxied `POST /v1/conversation`/`POST /v1/conversation/voice` call — do not
generate your own replacement key, or the exactly-once guarantee breaks.

## 11. Poll jobs

```
GET /v1/voice/jobs/{job_id}
Headers: x-api-key: <your VoiceBot credential>
```
Every 1–2s until `status` is terminal (`completed`/`failed`/`cancelled`). Proxy this
straight through to Flutter; there's nothing to transform.

## 12. Cancel jobs

```
POST /v1/voice/jobs/{job_id}/cancel
Headers: x-api-key: <your VoiceBot credential>
```
Proxy Flutter's cancel request through as-is.

## 13. Proxy protected audio safely

```
GET /v1/audio/{audio_id}
Headers: x-api-key: <your VoiceBot credential>
```
Stream the `audio/wav` bytes through to Flutter without buffering the whole file in
memory if avoidable. The retention window is short (~15 min default) — don't cache it
longer than that on your side either.

## 14. Never expose VoiceBot credentials

`x-api-key` and every provider credential stay server-side, in your own secret store.
Nothing in the response bodies above ever contains one (verified server-side, see
`VOICEBOT_INTEGRATION_GUIDE.md` §2) — but the discipline still has to hold on your side:
never echo the header you sent VoiceBot back to Flutter in a debug response, log line, or
error message.

## 15. Enforce patient scoping

Before proxying any request, confirm the `user_id` the caller wants matches what your own
session/auth layer says they're allowed to act as — VoiceBot's own check (is this
`user_id` in the key's authorized set?) is a second, independent layer, not a substitute
for yours.

## 16. Handle 401/403/404/409/413/422/429/5xx

See [`integration/voicebot-error-catalog.json`](integration/voicebot-error-catalog.json)
for the full table. Practically:
- `401`/`503` — your own configuration is broken (bad/missing credential); alert, don't
  surface to the patient.
- `403` — either your authorization check should have caught this first, or the patient
  is disabled; don't retry.
- `404` on job/audio — treat as gone, don't distinguish from cross-patient.
- `409` on conversation/voice — an idempotency conflict; surface to your own retry logic,
  don't blindly resend with a new key (that would double-execute).
- `409` on memory sync — a stale/conflicting revision; re-fetch your own source of truth
  and resend with a correct revision.
- `413`/`415`/`422` — a genuine client-side (Flutter) input problem; surface it back.
- `429` — back off and retry.
- `500` — log, alert, don't retry blindly in a loop.

## 17. Handle revocation

When a caregiver disables a patient in your system, call
`POST /v1/memory/sync` with `"active": false` (plus the required arrays, per the schema)
so VoiceBot's own conversational/voice/job/audio endpoints start refusing that patient
immediately, independent of whatever else changes in your own database.

## 18. Retry safely

See `VOICEBOT_INTEGRATION_GUIDE.md` §19. Side-effecting calls need
`X-Idempotency-Key`; polling doesn't.

## 19. Synchronize revisions

If you adopt `source_revision`, own the sequence yourself (VoiceBot never generates one) —
a sensible choice is your own `patients.content_version` (or an equivalent counter you
control), sent as-is on every full sync.

## 20. Handle stale/conflicting snapshots

- Stale (`409`, older revision than applied): you're behind — re-fetch your own source of
  truth and resend at the current revision.
- Conflicting (`409`, same revision + different content): your revision-generation logic
  has a bug — two different snapshots claimed the same revision number. Fix the sequence,
  don't just bump-and-retry blindly.

## 21. Log only safe metadata

Never log the full memory-sync payload, the full conversation response, or the `x-api-key`
you send. Log `user_id`, `request_id`, HTTP status, and timing — that's normally enough to
debug an integration issue without creating a second, uncontrolled copy of patient data.

## 22. Implement feature flags

A per-patient or global flag gating whether VoiceBot is actually called for this patient
yet is entirely your own concern — VoiceBot has no opinion about rollout sequencing.

## 23. Run staging acceptance

Use `../integration/fixtures/` for known-good request/response shapes while building your
gateway, and `../TESTING.md` for how to run a real VoiceBot instance in deterministic mode
(`SMRITI_LLM_PROVIDER=mock`, `SMRITI_TTS_PROVIDER=mock`) so your own CI can exercise the
full HTTP contract without needing real provider credentials.
