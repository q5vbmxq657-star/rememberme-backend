BEGIN;

ALTER TABLE digital_human_profile_erasure_requests
    ADD COLUMN IF NOT EXISTS resume_stage TEXT,
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;

ALTER TABLE digital_human_profile_erasure_requests
    DROP CONSTRAINT IF EXISTS profile_erasure_resume_stage_check;

ALTER TABLE digital_human_profile_erasure_requests
    ADD CONSTRAINT profile_erasure_resume_stage_check
    CHECK (
        resume_stage IS NULL
        OR resume_stage IN (
            'requested',
            'provider_cleanup',
            'storage_cleanup',
            'database_cleanup'
        )
    );

ALTER TABLE digital_human_profile_erasure_requests
    DROP CONSTRAINT IF EXISTS profile_erasure_attempt_count_check;

ALTER TABLE digital_human_profile_erasure_requests
    ADD CONSTRAINT profile_erasure_attempt_count_check
    CHECK (
        attempt_count >= 0
    );

ALTER TABLE digital_human_profile_erasure_requests
    DROP CONSTRAINT IF EXISTS profile_erasure_retry_authority_check;

ALTER TABLE digital_human_profile_erasure_requests
    ADD CONSTRAINT profile_erasure_retry_authority_check
    CHECK (
        (
            status = 'retryable_failed'
            AND resume_stage IS NOT NULL
        )
        OR
        (
            status <> 'retryable_failed'
            AND resume_stage IS NULL
            AND next_retry_at IS NULL
        )
    );

CREATE UNIQUE INDEX IF NOT EXISTS
    profile_erasure_single_active_profile_index
ON digital_human_profile_erasure_requests (
    profile_id
)
WHERE
    profile_id IS NOT NULL
    AND status <> 'completed';

CREATE INDEX IF NOT EXISTS
    profile_erasure_resume_queue_index
ON digital_human_profile_erasure_requests (
    status,
    next_retry_at,
    updated_at
)
WHERE status = 'retryable_failed';

INSERT INTO schema_migrations (
    version
)
VALUES (
    '007_profile_erasure_resume_authority'
)
ON CONFLICT (
    version
)
DO NOTHING;

COMMIT;
