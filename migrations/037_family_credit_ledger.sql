CREATE TABLE family_credit_entries (
    entry_id UUID PRIMARY KEY,
    family_id UUID NOT NULL REFERENCES family_groups(family_id) ON DELETE CASCADE,
    evidence_key TEXT NOT NULL UNIQUE CHECK (length(evidence_key) BETWEEN 1 AND 200),
    units BIGINT NOT NULL CHECK (units <> 0),
    kind TEXT NOT NULL CHECK (kind IN ('grant', 'usage')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((kind = 'grant' AND units > 0) OR (kind = 'usage' AND units < 0))
);
CREATE INDEX family_credit_entries_family ON family_credit_entries(family_id);
CREATE TABLE family_credit_reservations (
    call_id UUID PRIMARY KEY,
    family_id UUID NOT NULL REFERENCES family_groups(family_id) ON DELETE CASCADE,
    member_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    mode TEXT NOT NULL CHECK (mode IN ('voice', 'video')),
    reserved_units BIGINT NOT NULL CHECK (reserved_units > 0),
    consumed_units BIGINT CHECK (consumed_units >= 0 AND consumed_units <= reserved_units),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at TIMESTAMPTZ,
    CHECK ((settled_at IS NULL) = (consumed_units IS NULL))
);
CREATE INDEX family_credit_reservations_open ON family_credit_reservations(family_id) WHERE settled_at IS NULL;
INSERT INTO schema_migrations(version) VALUES ('037_family_credit_ledger');
