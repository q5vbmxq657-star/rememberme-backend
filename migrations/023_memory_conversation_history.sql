CREATE TABLE memory_conversation_history (
    profile_id UUID NOT NULL REFERENCES digital_human_profiles(profile_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    evidence_version JSONB NOT NULL,
    consent_revision BIGINT NOT NULL CHECK (consent_revision > 0),
    messages JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(messages) = 'array'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, user_id, conversation_id)
);

-- Remove stale derived text at the same commit that changes its authority.
CREATE FUNCTION invalidate_memory_conversation_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE memory_conversation_history SET messages='[]'::jsonb, revision=revision+1,
        updated_at=NOW() WHERE profile_id=NEW.profile_id;
    RETURN NEW;
END;
$$;
CREATE TRIGGER invalidate_chat_on_memory_change AFTER INSERT OR UPDATE
ON memory_index_generations FOR EACH ROW EXECUTE FUNCTION invalidate_memory_conversation_history();
CREATE TRIGGER invalidate_chat_on_consent_change AFTER UPDATE
ON profile_purpose_consents FOR EACH ROW EXECUTE FUNCTION invalidate_memory_conversation_history();

INSERT INTO schema_migrations(version) VALUES ('023_memory_conversation_history');
