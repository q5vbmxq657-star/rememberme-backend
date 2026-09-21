-- The canonical migration runner owns the transaction.
-- No names, provider identifiers, media paths or account credentials belong here.
-- This ledger must be replayed from a current independent copy before restoring service.
CREATE TABLE deletion_tombstones (
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('profile', 'account')),
    subject_digest BYTEA NOT NULL CHECK (octet_length(subject_digest) = 32),
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    backup_delete_by TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 days',
    PRIMARY KEY (subject_kind, subject_digest),
    CHECK (backup_delete_by <= deleted_at + INTERVAL '30 days')
);

CREATE FUNCTION enforce_deletion_tombstone() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    kind TEXT := TG_ARGV[0];
    identifier TEXT;
    fingerprint BYTEA;
BEGIN
    IF kind = 'profile' THEN
        identifier := CASE WHEN TG_OP = 'DELETE' THEN OLD.profile_id ELSE NEW.profile_id END::text;
    ELSE
        identifier := CASE WHEN TG_OP = 'DELETE' THEN OLD.user_id ELSE NEW.user_id END::text;
    END IF;
    fingerprint := sha256(convert_to(kind || ':' || identifier, 'UTF8'));
    PERFORM pg_advisory_xact_lock(hashtextextended(kind || ':' || identifier, 022));
    IF TG_OP = 'DELETE' THEN
        INSERT INTO deletion_tombstones(subject_kind, subject_digest)
        VALUES (kind, fingerprint) ON CONFLICT DO NOTHING;
        RETURN OLD;
    END IF;
    IF EXISTS (SELECT 1 FROM deletion_tombstones
               WHERE subject_kind = kind AND subject_digest = fingerprint) THEN
        RAISE EXCEPTION 'Deleted subject cannot be restored' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER profile_deletion_tombstone BEFORE INSERT OR DELETE OR UPDATE OF profile_id
ON digital_human_profiles FOR EACH ROW EXECUTE FUNCTION enforce_deletion_tombstone('profile');
CREATE TRIGGER account_deletion_tombstone BEFORE INSERT OR DELETE OR UPDATE OF user_id
ON users FOR EACH ROW EXECUTE FUNCTION enforce_deletion_tombstone('account');

CREATE FUNCTION minimize_completed_erasure() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'completed' THEN
        NEW.provider_snapshot := '{}'::jsonb;
        NEW.storage_asset_ids := '[]'::jsonb;
        NEW.error_code := NULL;
        NEW.error_message := NULL;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER minimize_completed_erasure BEFORE INSERT OR UPDATE
ON digital_human_profile_erasure_requests FOR EACH ROW EXECUTE FUNCTION minimize_completed_erasure();
UPDATE digital_human_profile_erasure_requests SET provider_snapshot = '{}'::jsonb,
    storage_asset_ids = '[]'::jsonb, error_code = NULL, error_message = NULL
WHERE status = 'completed';

INSERT INTO schema_migrations(version) VALUES ('022_deletion_retention_contract') ON CONFLICT DO NOTHING;
