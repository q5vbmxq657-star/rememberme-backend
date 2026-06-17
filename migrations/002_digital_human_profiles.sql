BEGIN;

CREATE TABLE IF NOT EXISTS digital_human_profiles (
    profile_id UUID PRIMARY KEY,

    quality_tier TEXT NOT NULL DEFAULT 'premium_presence',
    quality_percentage INTEGER NOT NULL DEFAULT 25
        CHECK (quality_percentage BETWEEN 0 AND 100),

    avatar_provider TEXT,
    avatar_replica_id TEXT,
    avatar_persona_id TEXT,
    avatar_training_job_id TEXT,
    avatar_training_status TEXT NOT NULL DEFAULT 'not_started',

    voice_provider TEXT,
    voice_id TEXT,
    voice_training_job_id TEXT,
    voice_training_status TEXT NOT NULL DEFAULT 'not_started',

    approved_portrait_url TEXT,

    consent_verified BOOLEAN NOT NULL DEFAULT FALSE,
    training_version INTEGER NOT NULL DEFAULT 1
        CHECK (training_version > 0),

    runtime_verified_at TIMESTAMPTZ,
    avatar_ready_at TIMESTAMPTZ,
    voice_ready_at TIMESTAMPTZ,

    last_error_code TEXT,
    last_error_message TEXT,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT digital_human_profiles_quality_tier_check
        CHECK (
            quality_tier IN (
                'signature_live',
                'expressive_live',
                'guided_live',
                'cinematic_portrait',
                'premium_presence',
                'blocked'
            )
        ),

    CONSTRAINT digital_human_profiles_avatar_status_check
        CHECK (
            avatar_training_status IN (
                'not_started',
                'collecting',
                'validating',
                'submitted',
                'training',
                'ready',
                'failed',
                'deleted'
            )
        ),

    CONSTRAINT digital_human_profiles_voice_status_check
        CHECK (
            voice_training_status IN (
                'not_started',
                'collecting',
                'validating',
                'submitted',
                'training',
                'ready',
                'failed',
                'deleted'
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS
    digital_human_profiles_avatar_replica_unique
ON digital_human_profiles (avatar_provider, avatar_replica_id)
WHERE avatar_replica_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
    digital_human_profiles_voice_unique
ON digital_human_profiles (voice_provider, voice_id)
WHERE voice_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    digital_human_profiles_avatar_status_index
ON digital_human_profiles (avatar_training_status);

CREATE INDEX IF NOT EXISTS
    digital_human_profiles_voice_status_index
ON digital_human_profiles (voice_training_status);

CREATE TABLE IF NOT EXISTS digital_human_training_jobs (
    job_id UUID PRIMARY KEY,
    profile_id UUID NOT NULL
        REFERENCES digital_human_profiles(profile_id)
        ON DELETE CASCADE,

    training_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_job_id TEXT,

    status TEXT NOT NULL,
    training_version INTEGER NOT NULL
        CHECK (training_version > 0),

    idempotency_key TEXT NOT NULL UNIQUE,

    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    provider_payload JSONB NOT NULL DEFAULT '{}'::jsonb,

    error_code TEXT,
    error_message TEXT,

    submitted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT digital_human_training_jobs_type_check
        CHECK (training_type IN ('avatar', 'voice')),

    CONSTRAINT digital_human_training_jobs_status_check
        CHECK (
            status IN (
                'created',
                'submitted',
                'training',
                'ready',
                'failed',
                'cancelled',
                'deleted'
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS
    digital_human_training_jobs_provider_job_unique
ON digital_human_training_jobs (provider, provider_job_id)
WHERE provider_job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    digital_human_training_jobs_profile_index
ON digital_human_training_jobs (
    profile_id,
    training_type,
    training_version
);

CREATE OR REPLACE FUNCTION
    rememberme_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS
    digital_human_profiles_set_updated_at
ON digital_human_profiles;

CREATE TRIGGER
    digital_human_profiles_set_updated_at
BEFORE UPDATE ON digital_human_profiles
FOR EACH ROW
EXECUTE FUNCTION rememberme_set_updated_at();

DROP TRIGGER IF EXISTS
    digital_human_training_jobs_set_updated_at
ON digital_human_training_jobs;

CREATE TRIGGER
    digital_human_training_jobs_set_updated_at
BEFORE UPDATE ON digital_human_training_jobs
FOR EACH ROW
EXECUTE FUNCTION rememberme_set_updated_at();

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations (version)
VALUES ('002_digital_human_profiles')
ON CONFLICT (version) DO NOTHING;

COMMIT;
