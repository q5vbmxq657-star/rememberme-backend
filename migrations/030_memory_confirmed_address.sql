ALTER TABLE memory_embeddings
    ADD COLUMN confirmed_address TEXT,
    ADD CONSTRAINT memory_confirmed_address_valid CHECK (
        confirmed_address IS NULL OR (
            char_length(confirmed_address) BETWEEN 1 AND 80
            AND confirmed_address = btrim(confirmed_address)
            AND confirmed_address !~ '[[:cntrl:]]'
        )
    );

INSERT INTO schema_migrations(version) VALUES ('030_memory_confirmed_address');
