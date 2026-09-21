CREATE TABLE account_erasure_requests (
    user_id UUID PRIMARY KEY,
    stage TEXT NOT NULL DEFAULT 'profiles' CHECK (stage IN ('profiles', 'apple', 'identity', 'completed')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0)
);
CREATE INDEX account_erasure_pending ON account_erasure_requests(retry_at) WHERE stage <> 'completed';
INSERT INTO schema_migrations(version) VALUES ('024_account_erasure_recovery');
