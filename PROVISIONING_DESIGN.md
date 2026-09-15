# Patient provisioning: pilot fixed-list mode vs. dynamic production mode

**Status: the dynamic mechanism described below is IMPLEMENTED**, not merely designed. It
was previously a future-design proposal in this document; it now exists in
`smriti_voice/api/dependencies.py`, `smriti_voice/api/routes/memory_sync.py`,
`smriti_voice/memory/repository.py`, and `database/migrations.py` (migration 7), with
automated test coverage in `tests/integration/test_dynamic_backend_key_provisioning.py`.

## Two modes, both real, chosen per API key

`SMRITI_API_KEYS` (a JSON object, one entry per key) supports three value shapes:

1. **Single patient**: `"the-key": "elder-1"` — one key, one fixed identity. Unchanged
   original design.
2. **Fixed allow-list**: `"the-key": ["elder-1", "elder-2"]` — one key, an explicit,
   hand-maintained list of patients. Adding a patient to this list still requires an
   env-var edit and a restart — this mode is unchanged and remains appropriate for a small,
   infrequently-changing pilot roster.
3. **Dynamic (production)**: `"the-key": {"dynamic": true}` (optionally with a seed
   `"user_ids": [...]`) — one key whose authorized patient set is the union of that seed
   list and whatever has been durably *granted* to this exact credential in the database.
   **This is the mode that removes the manual-env-var bottleneck.**

## How a dynamic key gains a new patient, with no env-var edit and no restart

1. Backend authenticates its own credential to VoiceBot with `x-api-key` (a dynamic key).
2. Backend calls `POST /v1/memory/sync` for a `user_id` (the Supabase patient UUID) this
   credential has never used before. Unlike a fixed-list key, a dynamic key is allowed to
   name a brand-new `user_id` here — this is the one and only place a dynamic key can
   introduce a patient it wasn't already authorized for.
3. Only if that sync call **succeeds** (not on a rejected/stale/conflicting one), VoiceBot
   records one row in `backend_key_grants`: `(SHA-256 hash of the key, user_id)`. This is
   an explicit, auditable grant — never a wildcard evaluated at request time. A dynamic
   key with zero grants and no seed list is authorized for nothing until its first
   successful sync, exactly like the original single-`user_id` auto-provisioning behavior.
4. Every subsequent request — `/v1/conversation`, `/v1/conversation/voice`, job polling,
   cancellation, audio retrieval — checks membership in the union of the seed list and the
   database grants for that credential. No route ever trusts a client-supplied `user_id`
   without this check.
5. `POST /v1/memory/sync` with `"active": false` disables the patient across every
   conversational/voice endpoint, independent of the grant — unchanged from before, and
   this is also today's revoke mechanism (see "What is still not built" below).

## What was preserved exactly, unchanged, from the original pilot design

- **Stable patient identity.** `user_id` remains VoiceBot's own primary key; a Supabase
  UUID is accepted as-is, with no translation layer.
- **Authorization separate from provisioning.** A grant does not create a memory row by
  itself, and a memory row's existence does not by itself grant access — these remain two
  separate checks (`authorized_user_ids` vs. `ensure_patient_active`/the row itself).
- **No wildcard, ever.** `"*"` or "this key plus any `user_id` the caller sends" was never
  implemented and still is not — every authorization, fixed-list or dynamic, is one
  specific (credential, `user_id`) pair, checked by membership.
- **No VoiceBot unrestricted Supabase access.** VoiceBot still never talks to Supabase
  directly; it only ever receives what the Backend sends it.
- **No credentials in Flutter or the browser.**

## What is still not built (honest gap, not silently implied)

- **No separate "revoke a grant" endpoint.** Today, "revoked" means `active: false`
  (blocks use) — the `backend_key_grants` row itself is never deleted, so a re-enabled
  patient regains access with no new grant needed. If a genuine "permanently forget this
  grant" operation is wanted later (distinct from disable/re-enable), it does not exist yet.
- **No credential-rotation tooling specific to dynamic keys beyond what already existed**
  (`tests/integration/test_credential_rotation.py` covers the original single/fixed-list
  modes; rotating a dynamic key's own secret value is an env-var change like any other key
  — the grants themselves, keyed by the old key's hash, would need re-granting under a new
  key's hash, which is not automated).
- **No dedicated audit log beyond process logs.** A grant is a normal database write, not
  yet mirrored to a separate audit trail.

## What this document is not

Not a commitment to build the two items above on any particular schedule — they are
recorded here as genuinely open, not silently closed by this change.
