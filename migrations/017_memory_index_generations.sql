-- The existing migration runner owns the transaction.
CREATE TABLE memory_index_generations (
    profile_id UUID PRIMARY KEY
        REFERENCES digital_human_profiles(profile_id) ON DELETE CASCADE,
    generation BIGINT NOT NULL CHECK (generation > 0),
    operation_id UUID NOT NULL,
    published_generation BIGINT NOT NULL DEFAULT 0
        CHECK (published_generation >= 0 AND published_generation <= generation),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations (version)
VALUES ('017_memory_index_generations');
