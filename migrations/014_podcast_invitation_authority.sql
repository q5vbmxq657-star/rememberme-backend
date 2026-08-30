CREATE TABLE IF NOT EXISTS podcast_invitations (
    invitation_id UUID PRIMARY KEY,
    profile_id UUID NOT NULL
        REFERENCES digital_human_profiles(profile_id)
        ON DELETE CASCADE,
    created_by_user_id UUID NOT NULL
        REFERENCES users(user_id)
        ON DELETE CASCADE,
    token_digest TEXT NOT NULL UNIQUE,
    requester_name TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    prompt_audio_asset_id UUID,
    response_audio_asset_id UUID,
    memory_id UUID NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    transcript TEXT,
    summary TEXT,
    memory_payload JSONB,
    safe_error_code TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT podcast_invitation_token_digest_check
        CHECK (token_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT podcast_invitation_status_check
        CHECK (status IN (
            'pending', 'recording', 'uploaded', 'processing',
            'completed', 'retryable_failed', 'expired'
        )),
    CONSTRAINT podcast_invitation_completion_check
        CHECK (
            (status = 'completed' AND completed_at IS NOT NULL
                AND transcript IS NOT NULL AND memory_payload IS NOT NULL)
            OR status <> 'completed'
        )
);

CREATE INDEX IF NOT EXISTS podcast_invitations_profile_status_index
ON podcast_invitations (profile_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS podcast_invitations_expiry_index
ON podcast_invitations (expires_at)
WHERE status NOT IN ('completed', 'expired');

DROP TRIGGER IF EXISTS podcast_invitations_set_updated_at
ON podcast_invitations;

CREATE TRIGGER podcast_invitations_set_updated_at
BEFORE UPDATE ON podcast_invitations
FOR EACH ROW
EXECUTE FUNCTION rememberme_set_updated_at();

INSERT INTO schema_migrations (version)
VALUES ('014_podcast_invitation_authority');
