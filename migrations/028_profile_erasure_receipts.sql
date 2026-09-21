CREATE TABLE profile_erasure_receipts (
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    profile_id UUID NOT NULL,
    request_id UUID NOT NULL REFERENCES digital_human_profile_erasure_requests(request_id) ON DELETE CASCADE,
    authorized_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(user_id, profile_id)
);

-- Only extant owner memberships constitute recoverable legacy provenance.
INSERT INTO profile_erasure_receipts(user_id, profile_id, request_id)
SELECT DISTINCT ON (m.user_id, m.profile_id) m.user_id, m.profile_id, e.request_id
FROM profile_memberships m JOIN digital_human_profile_erasure_requests e USING(profile_id)
WHERE m.role = 'owner' AND m.status = 'active'
ORDER BY m.user_id, m.profile_id, e.requested_at DESC;

INSERT INTO schema_migrations(version) VALUES ('028_profile_erasure_receipts');
