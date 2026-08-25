BEGIN;

CREATE TABLE IF NOT EXISTS
    digital_human_profile_erasure_requests (
        request_id UUID PRIMARY KEY,

        profile_id UUID
            REFERENCES digital_human_profiles(
                profile_id
            )
            ON DELETE SET NULL,

        idempotency_key TEXT NOT NULL UNIQUE,

        status TEXT NOT NULL
            DEFAULT 'requested',

        provider_snapshot JSONB NOT NULL
            DEFAULT '{}'::jsonb,

        storage_asset_ids JSONB NOT NULL
            DEFAULT '[]'::jsonb,

        error_code TEXT,
        error_message TEXT,

        requested_at TIMESTAMPTZ NOT NULL
            DEFAULT NOW(),

        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL
            DEFAULT NOW(),

        CONSTRAINT
            profile_erasure_status_check
        CHECK (
            status IN (
                'requested',
                'provider_cleanup',
                'provider_cleanup_required',
                'storage_cleanup',
                'database_cleanup',
                'retryable_failed',
                'completed'
            )
        )
    );

CREATE INDEX IF NOT EXISTS
    profile_erasure_profile_index
ON digital_human_profile_erasure_requests (
    profile_id,
    requested_at DESC
);

CREATE INDEX IF NOT EXISTS
    profile_erasure_retry_index
ON digital_human_profile_erasure_requests (
    status,
    updated_at
)
WHERE status IN (
    'requested',
    'provider_cleanup',
    'provider_cleanup_required',
    'storage_cleanup',
    'database_cleanup',
    'retryable_failed'
);

INSERT INTO schema_migrations (
    version
)
VALUES (
    '006_profile_erasure_requests'
)
ON CONFLICT (
    version
)
DO NOTHING;

COMMIT;
