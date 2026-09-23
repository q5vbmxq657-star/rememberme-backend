CREATE TABLE family_content (
    profile_id UUID NOT NULL REFERENCES digital_human_profiles(profile_id) ON DELETE CASCADE,
    memory_id UUID NOT NULL,
    author_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    family_id UUID REFERENCES family_groups(family_id) ON DELETE SET NULL,
    revision BIGINT NOT NULL CHECK (revision > 0),
    payload JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, memory_id),
    CHECK (payload IS NULL OR jsonb_typeof(payload) = 'object')
);
CREATE INDEX family_content_family_idx ON family_content(family_id, updated_at DESC)
    WHERE payload IS NOT NULL;

-- Withdrawal is durable: an old revision must not silently publish again.
CREATE FUNCTION withdraw_departed_family_content() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE family_content SET family_id=NULL, payload=NULL, revision=revision+1, updated_at=NOW()
    WHERE family_id=OLD.family_id AND author_id=OLD.user_id;
    RETURN OLD;
END;
$$;
CREATE TRIGGER family_content_member_departure BEFORE DELETE ON family_members
    FOR EACH ROW EXECUTE FUNCTION withdraw_departed_family_content();

CREATE FUNCTION withdraw_deleted_memory_family_content() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE family_content SET family_id=NULL, payload=NULL, revision=revision+1, updated_at=NOW()
    WHERE profile_id=NEW.profile_id AND memory_id::text=lower(NEW.memory_id);
    RETURN NEW;
END;
$$;
CREATE TRIGGER family_content_memory_deletion AFTER INSERT OR UPDATE ON memory_deletion_tombstones
    FOR EACH ROW EXECUTE FUNCTION withdraw_deleted_memory_family_content();

INSERT INTO schema_migrations(version) VALUES ('034_family_content');
