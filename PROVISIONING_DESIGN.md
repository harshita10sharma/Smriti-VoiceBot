# Patient provisioning: current pilot vs. future scalable design

**Status of this document: the "Future scalable design" section below is
FUTURE DESIGN. NOT IMPLEMENTED.** Nothing in that section exists in this
repository's code. This document exists so the shape of a later,
larger-scale mechanism is written down and can be reviewed jointly with
the Backend team before anyone builds it — it is a proposal to discuss,
not a spec already being executed against.

## Current pilot (implemented, in this repository today)

- **Server-side credential allow-list.** `SMRITI_API_KEYS`, a JSON object
  mapping each API key to one `user_id` or an explicit list of `user_id`s,
  set as a process environment variable. Adding, removing, or repointing a
  patient means editing this variable and restarting the process — there
  is no live admin API for it.
- **Stable patient identity.** `user_id` is VoiceBot's own primary key for
  a patient; any string matching `[A-Za-z0-9\-_.]{1,64}` is accepted,
  including a Supabase UUID sent as-is. Patient memory, sessions, jobs,
  and audio are all scoped to this identity, never to the credential used
  to reach it (see `test_credential_rotation.py` for the verified proof
  that rotating a key never loses a patient's memory).
- **Authorization separate from provisioning.** A `user_id` a key
  authorizes but that has never been synced or talked to yet is served
  normally; VoiceBot auto-provisions a bare row on first contact.
  Authorization is checked from the key config alone, never from whether
  that row exists.
- **Disable/revoke.** `POST /v1/memory/sync`'s `active` field disables (or
  re-enables) a patient across every conversational/voice endpoint,
  independent of the key allow-list.
- **This is genuinely acceptable for a pilot** (one deployment, a small
  number of patients, changes infrequent enough that manual env-var edits
  plus a restart are tolerable) but does not scale cleanly to many
  patients being added/removed/rotated frequently by a live Backend.

## Future scalable design (not implemented — for joint review)

A later mechanism, if/when pilot scale is outgrown, should preserve every
guarantee above while removing the manual-env-var bottleneck:

- **Backend-authenticated gateway remains the only caller.** VoiceBot's
  own credential model doesn't need to change shape — it still authorizes
  a caller for a set of patient identities. What changes is how that
  mapping is managed.
- **Server-side credential management**, likely backed by a small durable
  store (its own table, not Supabase — VoiceBot does not gain unrestricted
  Supabase access under this design either) that VoiceBot's auth
  dependency reads instead of a single environment variable, so adding a
  patient does not require a process restart.
- **Stable external patient UUID** — unchanged from today; still the
  caller's own identifier (e.g. the Supabase patient UUID), passed through
  as `user_id`.
- **Explicit provisioning and explicit authorization stay two separate
  steps**, exactly as today — provisioning a patient must never implicitly
  grant every key access to it.
- **Disable/revoke** — same guarantee as today, ideally exposed as an
  explicit operation rather than only reachable by resending a full memory
  snapshot.
- **Credential rotation** — a documented, ideally toolable, procedure with
  no memory loss, same as today's manual one but without a full-process
  restart.
- **Auditability** — who provisioned/revoked/rotated which patient's
  access, and when. Nothing today records this beyond process logs.
- **No credentials in Flutter or the browser** — unchanged; this was true
  before and remains non-negotiable after.
- **No VoiceBot unrestricted Supabase access** — unchanged; VoiceBot
  remains a separate service consuming a Backend-mediated contract, never
  a second writer against Supabase's own schema.

## What this document is not

This is not a commitment to build a specific technology (no database,
API shape, or library is prescribed here) and not a schedule. It is a
statement of the properties any future mechanism must keep, so a
scale-up conversation with the Backend team starts from an agreed list of
non-negotiables rather than from scratch.
