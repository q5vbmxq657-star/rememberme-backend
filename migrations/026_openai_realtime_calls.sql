-- Handles must survive profile/account deletion until provider cleanup is acknowledged.
CREATE TABLE openai_realtime_calls (
    session_id UUID PRIMARY KEY,
    profile_id UUID NOT NULL,
    user_id UUID NOT NULL,
    auth_session_id UUID NOT NULL,
    purpose_revision BIGINT NOT NULL,
    memory_version JSONB,
    model TEXT NOT NULL,
    voice TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    state TEXT NOT NULL DEFAULT 'reserved' CHECK (state IN
        ('reserved','creating','creation_unknown','active','hangup_requested','hangup_acknowledged','cancelled')),
    call_id TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    create_started_at TIMESTAMPTZ,
    hangup_requested_at TIMESTAMPTZ,
    hangup_acknowledged_at TIMESTAMPTZ,
    retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_until TIMESTAMPTZ,
    lease_token UUID,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX openai_realtime_calls_cleanup ON openai_realtime_calls (retry_at)
    WHERE hangup_acknowledged_at IS NULL;
CREATE INDEX openai_realtime_calls_profile ON openai_realtime_calls (profile_id);
INSERT INTO schema_migrations (version) VALUES ('026_openai_realtime_calls');
