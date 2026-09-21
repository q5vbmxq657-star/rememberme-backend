ALTER TABLE avatar_runtime_cleanup
    ADD COLUMN conversation_deleted BOOLEAN NOT NULL DEFAULT FALSE;

-- Ended calls still contain provider-side data. Reopen them for hard deletion.
UPDATE avatar_runtime_cleanup
SET completed_at=NULL, cleanup_requested=TRUE, retry_at=NOW(), lease_until=NULL, lease_token=NULL
WHERE completed_at IS NOT NULL AND (provider_create_started OR conversation_id IS NOT NULL);

ALTER TABLE avatar_runtime_cleanup
    ADD CONSTRAINT runtime_conversation_deletion_verified CHECK (
        NOT conversation_deleted OR (tavus_ended AND conversation_id IS NOT NULL)
    ),
    ADD CONSTRAINT runtime_completion_requires_deletion CHECK (
        completed_at IS NULL
        OR (NOT provider_create_started AND conversation_id IS NULL)
        OR (tavus_ended AND conversation_deleted AND conversation_id IS NOT NULL)
    );

INSERT INTO schema_migrations(version) VALUES ('029_runtime_conversation_deletion');
