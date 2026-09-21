# Deletion and backup contract

Maximum backup retention following deletion: 30 days. This is the authorized
product requirement, not evidence of the production backup configuration.

Migration 022 records a domain-separated SHA-256 digest of random profile/account
UUIDs and deletion timestamps atomically with row deletion. No media, names,
provider resource IDs or credentials are stored in this ledger. Tombstones are
not automatically expired: expiry would allow a stale client to recreate a deleted
UUID. Completed profile-erasure requests discard provider snapshots, storage asset
lists and error details. Their existing idempotency keys remain for retry identity;
those keys can contain UUIDs and must not be described as anonymous.

The ledger prevents reinsertion and ID changes to a deleted UUID. It does not
automatically repair an old restored database. Before enabling traffic after a
restore, operators must import the current ledger from an independent durable
copy and invoke DeletionRetentionService.assert_restore_clean(). A failure keeps
traffic disabled until restored deleted subjects and associated resources are
removed through verified cleanup. The backup's historical ledger alone is not
sufficient. Trigger-disabling restore tools require this explicit gate.

Current implementation always refuses restore release, including when no conflicting
rows are found: trusted independent ledger freshness/provenance verification is not
yet integrated. A clean SQL scan is diagnostic only. There is no caller flag to
bypass this missing evidence. This gate is not a completed operational restore flow.

ProfileErasureService.resume_pending() recovers due and interrupted requests using
the existing state machine. Session advisory locks prevent simultaneous execution
of one request and disappear when the owning DB connection/process exits. Provider
cleanup failures preserve the request for retry. Worker scheduling is integrated:
main.py starts run_erasure_recovery(), which runs profile/account recovery and
retention purge on a 30-second loop. This is code integration, not proof of
production execution. Provider operations must remain idempotent across a crash
between provider acknowledgement and DB persistence.

Historical ElevenLabs voice jobs are deleted before graph deletion, including
failed jobs with a recorded provider resource ID. Unknown in-flight creations
block deletion pending reconciliation. Required provider API:
ElevenLabsVoiceService.delete_voice_resource(*, voice_id: str) -> None,
an idempotent cleanup method independent of purpose grants and active voice state.
This API is integrated in the voice service.

Historical Tavus faces use delete_tavus_face(face_id=...) with hard=true to remove
associated training assets. The official contract was checked on 2026-09-21:
https://docs.tavus.io/api-reference/faces/delete-face
The method is available; the parent reported 10 passing provider tests. Face
deletion does not delete associated conversations, which require separate cleanup.

Migration 024 adds durable account recovery. Account deletion atomically records
the request and disables the account, then persists progress through profiles,
Apple revocation, identity deletion and completion. Recovery after identity deletion
does not require the deleted user row. Failed stages retain their checkpoint and
retry after five minutes. A process crash after Apple acknowledgement but before
the checkpoint repeats revocation, so Apple's idempotent revocation semantics remain
part of the provider acceptance test. Credentials are not copied into the queue.
AccountErasureService.resume_pending(limit=100) returns completed/pending counts and
is invoked by the integrated recovery worker. Session advisory locks serialize workers.

DeletionRetentionService.purge_completed_cleanup_records() removes runtime registry
entries whose completed_at is at least 30 days old, preserving incomplete and recent
entries. The integrated operational loop invokes it. This live-database purge does not
configure backup expiration.

The same purge removes unnecessary OpenAI call metadata and cancelled calls that
never started provider creation after 30 days. Unconfirmed provider handles remain
available for cleanup; hangup acknowledgement alone is not treated as termination.

Remaining operational proof: backup inventory and maximum 30-day TTL across all
copies/replicas, independent ledger replication, restore drill, production worker execution,
and provider acknowledgement.
No production deletions, backup settings or deployments were performed.

The policy for minors remains undecided and is not inferred from the 30-day choice.

Profile DELETE is authenticated and returns 204 only after persisted completion.
Migration 028 preserves authorized owner receipts for legacy requests without
changing their idempotency keys. Receipt lookup always requires a fresh valid user
session; foreign users receive the same denial as missing profiles. Migration
backfill uses only extant active owner memberships. Historical deletions whose
ownership evidence was already destroyed cannot be assigned to a caller by guess.

Profile erasure requests both runtime and OpenAI cleanup before provider deletion
and checks again before graph deletion. Creating/unknown OpenAI calls and calls
without verified termination block completion, including hangup acknowledgements.
Never-started cancelled reservations do not block. The present OpenAI registry has
no verified terminal proof for started calls; these remain pending for provider
acceptance rather than being falsely reported as erased.

Training creation and avatar activation fences are integrated by the coordinating
workstreams. Existing created/in-flight training jobs continue to block deletion
until their remote resource outcome has been reconciled. No production provider
or device acceptance is asserted by the local tests.
