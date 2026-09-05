-- The migration runner owns the transaction.

ALTER TABLE digital_human_profiles
    DROP CONSTRAINT IF EXISTS
        digital_human_profiles_voice_status_check;

ALTER TABLE digital_human_profiles
    ADD CONSTRAINT
        digital_human_profiles_voice_status_check
    CHECK (
        voice_training_status IN (
            'not_started',
            'collecting',
            'validating',
            'submitted',
            'training',
            'verification_required',
            'ready',
            'failed',
            'deleted'
        )
    );

ALTER TABLE digital_human_training_jobs
    DROP CONSTRAINT IF EXISTS
        digital_human_training_jobs_status_check;

ALTER TABLE digital_human_training_jobs
    ADD CONSTRAINT
        digital_human_training_jobs_status_check
    CHECK (
        status IN (
            'created',
            'submitted',
            'training',
            'verification_required',
            'ready',
            'failed',
            'cancelled',
            'deleted'
        )
    );

INSERT INTO schema_migrations (
    version
)
VALUES (
    '016_voice_verification_status'
)
ON CONFLICT (version) DO NOTHING;
