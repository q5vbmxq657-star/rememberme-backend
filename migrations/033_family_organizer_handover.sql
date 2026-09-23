CREATE TABLE family_organizer_transfers (
    family_id UUID PRIMARY KEY REFERENCES family_groups(family_id) ON DELETE CASCADE,
    transfer_id UUID NOT NULL UNIQUE,
    candidate_id UUID NOT NULL REFERENCES family_members(user_id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '3 days'
);
CREATE INDEX family_transfer_expiry ON family_organizer_transfers(expires_at);
INSERT INTO schema_migrations(version) VALUES ('033_family_organizer_handover');
