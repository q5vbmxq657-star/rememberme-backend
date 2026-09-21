ALTER TABLE avatar_runtime_cleanup
    ADD COLUMN provider_create_started BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN conversation_name TEXT UNIQUE;
-- Old workers may already have sent a create request without a known response.
UPDATE avatar_runtime_cleanup SET provider_create_started=worker_started;
INSERT INTO schema_migrations(version) VALUES ('025_runtime_creation_correlation');
