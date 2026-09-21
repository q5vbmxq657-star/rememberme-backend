-- The existing migration runner owns the transaction.
CREATE INDEX idx_memory_embeddings_profile_id_normalized
    ON memory_embeddings (lower(profile_id));

INSERT INTO schema_migrations (version)
VALUES ('018_memory_profile_lookup');
