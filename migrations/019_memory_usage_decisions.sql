-- The existing migration runner owns the transaction.
CREATE TABLE memory_usage_decisions (
    profile_id UUID NOT NULL REFERENCES digital_human_profiles(profile_id) ON DELETE CASCADE,
    memory_id TEXT NOT NULL CHECK (memory_id = lower(btrim(memory_id)) AND length(memory_id) BETWEEN 1 AND 200),
    included BOOLEAN NOT NULL,
    revision BIGINT NOT NULL CHECK (revision > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, memory_id)
);

INSERT INTO schema_migrations (version) VALUES ('019_memory_usage_decisions');
