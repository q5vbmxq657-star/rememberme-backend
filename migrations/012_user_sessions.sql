CREATE TABLE IF NOT EXISTS user_sessions (
    session_id UUID PRIMARY KEY,

    user_id UUID NOT NULL
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    refresh_token_hash TEXT NOT NULL UNIQUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    access_expires_at TIMESTAMPTZ NOT NULL,
    refresh_expires_at TIMESTAMPTZ NOT NULL,

    revoked_at TIMESTAMPTZ,

    last_rotated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS
    idx_user_sessions_user_id
ON user_sessions(user_id);

CREATE INDEX IF NOT EXISTS
    idx_user_sessions_active_refresh
ON user_sessions(refresh_token_hash)
WHERE revoked_at IS NULL;

INSERT INTO schema_migrations (
    version
)
VALUES (
    '012_user_sessions'
);
