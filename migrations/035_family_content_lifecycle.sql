CREATE FUNCTION withdraw_closed_family_content() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE family_content SET family_id=NULL,payload=NULL,revision=revision+1,updated_at=NOW()
    WHERE family_id=OLD.family_id;
    RETURN OLD;
END;
$$;
CREATE TRIGGER family_content_group_closure BEFORE DELETE ON family_groups
    FOR EACH ROW EXECUTE FUNCTION withdraw_closed_family_content();

CREATE FUNCTION withdraw_revoked_profile_content() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR NEW.status <> 'active' OR NEW.role <> 'owner' THEN
        UPDATE family_content SET family_id=NULL,payload=NULL,revision=revision+1,updated_at=NOW()
        WHERE profile_id=OLD.profile_id AND author_id=OLD.user_id AND payload IS NOT NULL;
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER family_content_profile_revocation AFTER DELETE OR UPDATE ON profile_memberships
    FOR EACH ROW EXECUTE FUNCTION withdraw_revoked_profile_content();

INSERT INTO schema_migrations(version) VALUES ('035_family_content_lifecycle');
