CREATE TABLE self_hosted_voice_references (
    job_id UUID PRIMARY KEY REFERENCES digital_human_training_jobs(job_id) ON DELETE CASCADE,
    profile_id UUID NOT NULL REFERENCES digital_human_profiles(profile_id) ON DELETE CASCADE,
    consent_revision BIGINT NOT NULL CHECK (consent_revision > 0),
    model_revision TEXT NOT NULL CHECK (model_revision ~ '^[a-f0-9]{64}$'),
    encryption_key_id TEXT NOT NULL,
    ciphertext BYTEA NOT NULL CHECK (octet_length(ciphertext) BETWEEN 96000 AND 1000000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX self_hosted_voice_references_profile ON self_hosted_voice_references(profile_id);
INSERT INTO schema_migrations(version) VALUES ('038_self_hosted_voice_references');
