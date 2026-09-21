CREATE TABLE profile_purpose_consents (
    profile_id UUID PRIMARY KEY REFERENCES digital_human_profiles(profile_id) ON DELETE CASCADE,
    revision BIGINT NOT NULL CHECK (revision > 0),
    policy_version TEXT NOT NULL,
    purposes TEXT[] NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (purposes <@ ARRAY['photo_likeness', 'video_motion', 'voice_synthesis', 'memory_context', 'provider_processing']::TEXT[])
);

CREATE TABLE profile_purpose_consent_events (
    profile_id UUID NOT NULL REFERENCES digital_human_profiles(profile_id) ON DELETE CASCADE,
    revision BIGINT NOT NULL CHECK (revision > 0),
    actor_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    policy_version TEXT NOT NULL,
    purposes TEXT[] NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, revision)
);

INSERT INTO schema_migrations (version) VALUES ('020_profile_purpose_consents');
