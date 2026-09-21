CREATE TABLE memory_deletion_tombstones (
    profile_id UUID NOT NULL REFERENCES digital_human_profiles(profile_id) ON DELETE CASCADE,
    memory_id TEXT NOT NULL CHECK (memory_id = lower(btrim(memory_id)) AND length(memory_id) BETWEEN 1 AND 200),
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, memory_id)
);

CREATE FUNCTION reject_deleted_memory_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Match publication/deletion lock order; the advisory lock covers legacy profiles without a generation.
    PERFORM 1 FROM memory_index_generations WHERE profile_id=NEW.profile_id::uuid FOR UPDATE;
    PERFORM pg_advisory_xact_lock(hashtextextended(lower(NEW.profile_id) || ':' || lower(btrim(NEW.memory_id)), 027));
    IF EXISTS (SELECT 1 FROM memory_deletion_tombstones
               WHERE profile_id=NEW.profile_id::uuid AND memory_id=lower(btrim(NEW.memory_id))) THEN
        RAISE EXCEPTION 'Deleted memory cannot be restored' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER reject_deleted_memory_write BEFORE INSERT OR UPDATE ON memory_embeddings
FOR EACH ROW EXECUTE FUNCTION reject_deleted_memory_write();

INSERT INTO schema_migrations(version) VALUES ('027_memory_deletion_tombstones');
