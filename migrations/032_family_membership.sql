CREATE TABLE family_groups (
    family_id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    organizer_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE family_members (
    user_id UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    family_id UUID NOT NULL REFERENCES family_groups(family_id) ON DELETE CASCADE,
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 60),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX family_members_group ON family_members(family_id);
CREATE TABLE family_invitations (
    invitation_id UUID PRIMARY KEY,
    family_id UUID NOT NULL REFERENCES family_groups(family_id) ON DELETE CASCADE,
    token_digest TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    claimant_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
    claimant_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((claimant_id IS NULL) = (claimant_name IS NULL))
);
CREATE INDEX family_invitations_group ON family_invitations(family_id);
CREATE UNIQUE INDEX family_one_pending_claim ON family_invitations(claimant_id) WHERE claimant_id IS NOT NULL;
INSERT INTO schema_migrations(version) VALUES ('032_family_membership');
