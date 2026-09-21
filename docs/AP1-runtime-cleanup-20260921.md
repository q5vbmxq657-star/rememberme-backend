# Durable runtime cleanup integration

Migration 021 adds `avatar_runtime_cleanup`; migration 025 adds creation intent
and opaque correlation. Apply through the existing migration
runner after 020, before deploying API and worker together. The worker now needs
DATABASE_URL for the same database as the API. No tokens, media or conversation
content are stored. Rows deliberately survive profile deletion.

The adapter records the deterministic LiveKit room before creating remote
resources. The worker reserves activation exactly once, records the returned
Tavus conversation ID, and records a confirmed end. Close requests remain pending
until termination can be verified. Profile-scoped close remains resolvable from
the database after the API restarts.

FastAPI lifespan starts RuntimeCleanupService.run and cancels it on shutdown.
Recovery polls every five seconds, claims one row using SKIP LOCKED and a unique
two-minute lease, retries failed work with capped backoff, and prevents stale
workers from acknowledging a newer claim. LiveKit and Tavus cleanup are attempted
independently. Missing Tavus IDs never count as successful cleanup after worker
activation. Ordinary active sessions become eligible at token expiry; requested,
revoked and erased sessions become eligible immediately on the next poll.

Purpose requirements are derived from the persisted Tavus training request:
train_image_url -> photo_likeness; train_video_url -> video_motion. Missing source
provenance fails closed. Runtime also requires voice_synthesis because it
transports synthesized speech. It sends no memory context itself. The existing
purpose authorization helper additionally requires provider_processing. The
consent revision is persisted and checked before dispatch, worker activation,
client credential return, and each audio submission. Recovery detects changed
revisions, missing grants, erasure requests and deleted profiles.

Integration hook: RuntimeCleanupRepository.request_profile(profile_id) durably
requests closure. Consent/erasure code may call this for immediate scheduling;
the recovery scan independently detects those changes. Status and cleanup do
not require an active purpose grant.

## Explicit boundaries

- Creation intent is recorded immediately before the existing SDK API call. A
  pre-create connection failure is therefore cleanable without a Tavus ID.
  Migration 025 conservatively treats legacy started workers as attempted creates.
- New sessions use an opaque `stay_<random UUID>` conversation_name. Recovery
  paginates GET /v2/conversations and binds only an exact unique name match.
  Missing, ambiguous, malformed or incomplete results remain pending. Legacy
  sessions without correlation still require manual provider reconciliation.
- The SDK AvatarSession lacks a public API injection hook. A narrow local
  subclass wraps its private _api slot; an installed-SDK contract test verifies
  the supported extra_payload argument and one-attempt connection options.
  No SDK source is edited and the SDK still owns the media pipeline.
- The unavoidable interval between authorization and a remote request is not a
  distributed transaction. A changed grant schedules termination; already-sent
  data cannot be recalled.
- This registry covers Tavus/LiveKit runtime sessions. OpenAI realtime sessions,
  orphan ElevenLabs training voices, replicas and training jobs are outside this
  implementation. They need provider-specific durable cleanup integration.
- No production migration, deployment, provider mutation or real-device acceptance
  was performed. One explicitly authorized account GET list probe returned 200
  and confirmed data/total_count and correlation fields; output contained schema
  booleans only. This work alone does not complete AP1.
- Completed registry-row retention must be integrated with the central retention
  contract. Pending rows must remain until cleanup is resolved.

Tests in test_runtime_cleanup_durable.py use isolated PostgreSQL schemas and
mock provider operations. Existing test_avatar_runtime_cleanup_contract.py
continues to cover resource deletion and matching terminal Tavus status.

Official contract checked 2026-09-21:
https://docs.tavus.io/api-reference/conversations/create-conversation
https://docs.tavus.io/api-reference/conversations/get-conversations
