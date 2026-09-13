# Flutter ↔ VoiceBot Integration

**Audience:** the Flutter developer building the patient-facing tablet app. This is
implementation-focused; for the full reference, see
[`VOICEBOT_INTEGRATION_GUIDE.md`](VOICEBOT_INTEGRATION_GUIDE.md).

**You never call VoiceBot directly.** Every call in this document goes to your own
Backend, which proxies to VoiceBot. This document uses VoiceBot's field names because
that's what your Backend's proxied responses will contain — not because you talk to
VoiceBot yourself.

## 1. Authenticate with your existing Backend

Unchanged — your existing Supabase-backed auth/session flow. VoiceBot has no visibility
into this at all.

## 2. Never store a VoiceBot credential

There is no VoiceBot credential in the app, ever. Your Backend holds `x-api-key`
server-side. If any part of your app configuration ever contains an `x-api-key`-shaped
value, that is a bug to fix before shipping, not a valid shortcut.

## 3. Retain the VoiceBot session ID

Store `session_id` (a string) per active conversation. Send it back on every follow-up
turn in the same conversation; send `null` to start a new one. Discard it when the device
is re-paired to a different patient — a new pairing must start a new session, never reuse
an old one across identities.

## 4. What to send for a text turn (via your Backend)

```dart
// pseudocode — not a real SDK, your Backend defines the actual proxy shape
final response = await backend.post('/proxy/conversation', body: {
  'session_id': currentSessionId,       // or null
  'message': userTypedText,
  'language': currentPatientLanguage,   // optional
});
```

## 5. What to send for voice (via your Backend)

Record one WAV per utterance on an explicit tap (never continuous listening). Format
requirements (enforced by VoiceBot, your Backend just proxies them through):

| Property | Requirement |
|---|---|
| Container | RIFF/WAVE |
| Recommended | 16 kHz, mono, 16-bit PCM (safest for ASR quality; not strictly enforced, but don't deviate without a reason) |
| Max size | 10 MiB by default |
| Max duration | 60s by default |

```dart
// pseudocode
final response = await backend.postMultipart('/proxy/conversation/voice', fields: {
  'session_id': currentSessionId,
  'language': currentPatientLanguage,
  'speak': 'true',
}, file: recordedWavFile);
```

## 6. Handling the initial response

`response_text` arrives synchronously in the same response — **show it immediately**,
regardless of whether speech synthesis is still running. Never block the UI waiting for
audio.

If `speak: true` was requested, the response also carries a `job_id` and
`job_status: "QUEUED"` — that's your cue to start polling (§7).

## 7. Polling a voice job

```dart
// pseudocode
Timer.periodic(Duration(seconds: 1), (timer) async {
  final job = await backend.get('/proxy/voice/jobs/$jobId');
  if (job['status'] == 'completed') {
    timer.cancel();
    await playAudio(job['audio_url'], job['audio_id']);
  } else if (job['status'] == 'failed' || job['status'] == 'cancelled') {
    timer.cancel();
    // response_text from step 6 is still valid and already shown — nothing more to do
  }
});
```
Stop polling the instant `status` is terminal (`completed`/`failed`/`cancelled`) — don't
keep polling a job that's already done.

## 8. Retrieving/playing audio

Fetch `GET /v1/audio/{audio_id}` (via your Backend proxy) **once, promptly**, after
`status == "completed"`. It's raw `audio/wav` bytes. If the fetch 404s, or the job later
reports `audio_expired: true`, that's not an error — the audio aged out of a short
retention window (~15 min). Fall back to text-only silently; `response_text` from step 6
is unaffected.

## 9. Cancelling

```dart
// pseudocode
await backend.post('/proxy/voice/jobs/$jobId/cancel');
```
Call this when the user backs out of a request before it completes — don't just stop
polling and walk away, since an un-cancelled job keeps synthesizing server-side for no
one.

## 10. Discarding obsolete results

Track which `job_id`/`session_id` your UI currently cares about. If a poll result arrives
for a `job_id` you've already cancelled or superseded (the user tapped again, navigated
away, or the app re-paired), ignore it client-side — the server-side state is already
correct (§9's cancel call), but your own UI state should never resurrect a result it
already gave up on.

## 11. If the user navigates away mid-request

Cancel the in-flight job (§9) if one exists. Discard the pending UI state. Do not assume
the turn "still happened somewhere" — check the actual response if you need to know.

## 12. If the network disappears

The HTTP call itself fails/times out. Show a retry affordance. **Do not assume anything
executed** — a network failure before a response arrives means you genuinely don't know
whether the server-side turn ran; don't guess "probably succeeded" or "probably didn't."
If the interaction was one you'd retry, use the same `X-Idempotency-Key` your Backend used
(coordinate this with your Backend's retry contract, §18-19 of
`BACKEND_VOICEBOT_INTEGRATION.md`) so a resend can't double-execute a confirmed action.

## 13. If the patient is re-paired

Discard the current `session_id` entirely and start fresh (§3). A session from before
re-pairing must never be reused — it belongs to whatever patient identity was active when
it was created.

## 14. Alarm priority

Medication-reminder/alarm audio takes priority over AI playback — this is entirely your
app's responsibility; VoiceBot has no concept of device alarms at all and cannot enforce
this for you.

## 15. Audio overlap prevention

Don't let a memo, a game's sound, and VoiceBot's TTS audio play simultaneously — arbitrate
this the same way you already arbitrate between your existing local audio sources.
VoiceBot's audio is just another WAV file to your player; nothing about it is special
beyond "fetch once, promptly."

## 16. Navigation action mapping

| VoiceBot `action` | Your screen |
|---|---|
| `OPEN_PLAY` | Games |
| `OPEN_MY_PEOPLE` | People |
| `OPEN_TODAY` | Today/routine |
| `OPEN_MEDICINE` | Medicines |
| `HELP` | (spoken only, no navigation) |
| `STOP` | (stop current activity, no navigation) |

Only navigate when `action_accepted == true`. Never navigate based on `response_text` or
raw transcript content — those are conversational text, never commands.

## 17. Action confirmation

When a response has `kind == "CONFIRMATION"` and `requires_confirmation == true`, show the
confirmation prompt (`response_text` already carries it, in the patient's language) and
wait for an explicit "yes"/"no" turn — never auto-confirm, never treat silence as an
answer. See `VOICEBOT_INTEGRATION_GUIDE.md` §13 for the full state table.

## 18. Calling/reminder actions

Both remain **disabled** until a real, authorized executor exists on the Backend/Flutter
side. If `call_family_member` or `create_reminder` ever return `action_accepted: true`
today, that only means the deterministic gate authorized the *proposal* — it is explicitly
not proof a call was placed or an alarm was scheduled (see
`VOICEBOT_INTEGRATION_GUIDE.md` §15–16). Do not build a UI that implies otherwise.

## 19. Error handling

See `VOICEBOT_INTEGRATION_GUIDE.md` §6 for the full catalogue. From Flutter's side, the
practically important ones: `403` — stop, don't retry, likely a pairing/authorization
problem your Backend should explain; `404` on a job/audio id — treat as gone; `409` on a
side-effecting call your Backend retried — surface it as "already in progress" rather
than a hard failure; `429` — your Backend should already be backing off, you shouldn't
need to react to this directly.

## 20. Timeout handling

Set a client-side timeout on the initial conversation/voice call sensible for your users
(a few seconds of silence feels long to an elderly user); on timeout, show a retry
affordance rather than assuming failure (§12).

## 21. Retry behavior

Don't blindly retry a voice/conversation call without your Backend attaching an
idempotency key — a naive retry of a side-effecting call can double-execute a just-confirmed
action. Polling and audio fetch are safe to retry freely.

## 22. Offline behavior

Your existing local reminders, games, and downloaded media must keep working entirely
independently of VoiceBot's reachability — VoiceBot going unreachable should never disable
anything that doesn't actually depend on it. See `VOICEBOT_INTEGRATION_GUIDE.md` §17 for
the full offline/degraded matrix.

## 23. No secrets in app

No VoiceBot credential, no provider credential (Groq/Sarvam/etc.), anywhere in the app
binary, its configuration, or its logs. Everything patient-identifying that leaves the
device goes to your own Backend over your existing authenticated channel.
