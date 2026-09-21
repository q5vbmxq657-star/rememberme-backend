-- Intentionally no cascading profile FK: cleanup must survive profile deletion.
CREATE TABLE avatar_runtime_cleanup (
    session_id TEXT PRIMARY KEY,
    profile_id UUID NOT NULL,
    room_name TEXT NOT NULL UNIQUE,
    dispatch_id TEXT,
    purpose_revision BIGINT NOT NULL,
    purposes TEXT[] NOT NULL,
    conversation_id TEXT UNIQUE,
    worker_started BOOLEAN NOT NULL DEFAULT FALSE,
    tavus_ended BOOLEAN NOT NULL DEFAULT FALSE,
    cleanup_requested BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_until TIMESTAMPTZ,
    lease_token UUID,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX avatar_runtime_cleanup_pending ON avatar_runtime_cleanup(retry_at)
    WHERE completed_at IS NULL;
INSERT INTO schema_migrations(version) VALUES ('021_runtime_session_registry');
