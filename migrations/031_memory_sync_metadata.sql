ALTER TABLE memory_embeddings
    ADD COLUMN sync_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD CONSTRAINT memory_sync_metadata_object CHECK (jsonb_typeof(sync_metadata) = 'object');

INSERT INTO schema_migrations(version) VALUES ('031_memory_sync_metadata');
