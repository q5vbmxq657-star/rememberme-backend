BEGIN;

CREATE TABLE IF NOT EXISTS
    digital_human_generated_preview_jobs (
        job_id UUID PRIMARY KEY,

        profile_id UUID NOT NULL
            REFERENCES digital_human_profiles(
                profile_id
            )
            ON DELETE CASCADE,

        training_version INTEGER NOT NULL
            CHECK (
                training_version > 0
            ),

        package_record_id TEXT NOT NULL,

        provider TEXT NOT NULL,
        replica_id TEXT NOT NULL,
        provider_video_id TEXT,

        status TEXT NOT NULL,

        provider_payload JSONB NOT NULL
            DEFAULT '{}'::jsonb,

        generated_asset_id UUID
            REFERENCES avatar_evidence_assets(
                asset_id
            )
            ON DELETE SET NULL,

        media_sha256 TEXT,
        media_content_type TEXT,
        media_size_bytes BIGINT,

        error_code TEXT,
        error_message TEXT,

        submitted_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        materialized_at TIMESTAMPTZ,

        created_at TIMESTAMPTZ NOT NULL
            DEFAULT NOW(),

        updated_at TIMESTAMPTZ NOT NULL
            DEFAULT NOW(),

        CONSTRAINT
            generated_preview_status_check
            CHECK (
                status IN (
                    'created',
                    'submitted',
                    'generating',
                    'materializing',
                    'ready',
                    'failed',
                    'cancelled',
                    'stale'
                )
            )
    );

CREATE UNIQUE INDEX IF NOT EXISTS
    generated_preview_provider_video_unique
ON digital_human_generated_preview_jobs (
    provider,
    provider_video_id
)
WHERE provider_video_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    generated_preview_profile_version_index
ON digital_human_generated_preview_jobs (
    profile_id,
    training_version,
    created_at DESC
);

DROP TRIGGER IF EXISTS
    generated_preview_set_updated_at
ON digital_human_generated_preview_jobs;

CREATE TRIGGER
    generated_preview_set_updated_at
BEFORE UPDATE
ON digital_human_generated_preview_jobs
FOR EACH ROW
EXECUTE FUNCTION rememberme_set_updated_at();

INSERT INTO schema_migrations (
    version
)
VALUES (
    '005_generated_preview_jobs'
)
ON CONFLICT (
    version
)
DO NOTHING;

COMMIT;
