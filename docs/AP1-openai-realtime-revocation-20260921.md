# OpenAI Realtime: durable revocation integration

Implementation handoff, 2026-09-21. Not a real-provider acceptance or an AP1 completion statement.

## iOS contract

- POST /v1/realtime/avatar/session: existing profile metadata; authenticated STAY principal.
  Required header X-STAY-Realtime-Protocol: stay_sdp. Missing/other header returns
  HTTP 426 with detail.code=app_update_required. No legacy secret path remains.
- Reservation response: session_id UUID, profile_id UUID, model, voice,
  connection_mode=stay_sdp, transport=webrtc, session_type=openai_realtime_avatar,
  fallback_mode=native_auto_turn_voice. No client_secret or dummy credential.
- POST /v1/realtime/avatar/sessions/{session_id}/connect: JSON {offer_sdp:string}.
  Response: {session_id,profile_id,answer_sdp}. Bound to original user and STAY
  authentication session; no client-supplied conversation or memory context.
- DELETE same session URL: HTTP 202; {session_id,profile_id,state,hangup_acknowledged}.
  Ownership is checked without a profile/purpose guard, so profile erasure does not
  prevent cleanup. Normal authentication remains mandatory. Revoked authentication
  is handled by the server sweep, not by bypassing authentication on DELETE.

Reservation causes no provider request. Connection retrieves current canonical
memory evidence and checks profile, purpose revision and memory version before
and after the provider response. The server sends multipart SDP/session config
to OpenAI, records Location call ID before delivering the SDP answer. Client
disconnect is shielded through bounded creation/registration and requests cleanup.
Concurrent/repeated connect does not create a second call. Lost response remains
creation_unknown; there is no blind retry or invented reconciliation API.

## Parent integration

Migration: 026_openai_realtime_calls.sql. Existing 025_runtime_creation_correlation.sql
belongs to the parallel runtime work and was left untouched. The migration runner
requires unique consecutive numeric versions.

Lifespan runs OpenAIRealtimeCleanupService().run(), cancelling/awaiting its task
on shutdown. Constructor/run failures require parent startup retry. This task did
not edit main.py. recover_once() sweeps durable permissions/memory/auth/erasure
state, claims one job with a lease, calls hangup and records acknowledgment only
on HTTP 200. Expired leases are reclaimed; stale workers cannot finish newer jobs.
The sweep interval when idle is five seconds, not an instantaneous revocation SLA.

Registry methods for erasure integration:
- request_profile(profile_id): persist cleanup intent for all owned calls.
- profile_cleanup_status(profile_id): counts termination_unconfirmed,
  creation_unknown and hangup_acknowledged; confirmed_terminated boolean.
- require_profile_terminated(profile_id): request cleanup and raise
  RealtimeStateConflict if external termination remains unconfirmed.

The registry intentionally has no cascading FK to erased profiles/users, so
external cleanup handles survive graph deletion. Profile/account deletion should
use the gate before final completion. Brief initial metadata is cleared at connect
or cleanup; no memory text, SDP, audio or API key is persisted in this table.

## Remaining blocking gap

Durable revocation implementation is NOT verified provider closure.
OpenAI documents HTTP 200 from hangup as beginning teardown. This implementation
therefore records hangup_acknowledged, never closed/completed. It provides no
verified terminal provider observation. After any external create attempt, the
strict erasure gate remains blocked even after HTTP 200. In the present integration
that block cannot automatically clear. This is an explicit outstanding AP1 blocker,
not a successful deletion or a production-ready complete erasure flow.

No 404-as-success, hypothetical TTL-as-proof or websocket-disconnect-as-proof is
used. creation_unknown also remains unresolved indefinitely if no call ID was
received. A process failure after provider acceptance but before durable handle
storage cannot be eliminated by local transactions; no provider idempotency or
lookup contract is assumed. Real provider tests and a documented terminal-evidence
contract are required to close these gaps. No customer media or real calls were
sent during this implementation; no deployment or TestFlight upload occurred.

Official references previously fetched on 2026-09-21:
- https://developers.openai.com/api/docs/guides/voice-webrtc?api=realtime
- https://developers.openai.com/api/docs/guides/voice-server-controls?api=realtime
- https://developers.openai.com/api/reference/resources/realtime/subresources/calls/methods/hangup
- https://developers.openai.com/api/docs/guides/voice-sip?api=realtime

## Verification

Dedicated tests cover registry persistence/restart, consent/memory/profile/auth
revocation, stale cleanup leases, ownership, update intent, secret-free reservation,
SDP exchange, stale evidence, provider errors, lost creation, client cancellation,
cleanup during erasure and the intentionally blocked post-ack deletion gate.
Existing realtime security tests were migrated to connect; assertions still deny
result delivery after authorization/evidence revocation and verify cleanup intent.
Provider HTTP is mocked; PostgreSQL persistence tests use an isolated local schema.
Final counts and JUnit path are supplied in the implementation handoff.
