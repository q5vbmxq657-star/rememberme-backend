CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY,

    status TEXT NOT NULL DEFAULT 'active'
        CHECK (
            status IN (
                'active',
                'disabled',
                'deleted'
            )
        ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS external_user_identities (
    identity_id UUID PRIMARY KEY,

    user_id UUID NOT NULL
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    provider TEXT NOT NULL
        CHECK (
            provider IN (
                'apple'
            )
        ),

    provider_subject TEXT NOT NULL
        CHECK (
            BTRIM(provider_subject) <> ''
        ),

    email TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT external_user_identities_provider_subject_unique
        UNIQUE (
            provider,
            provider_subject
        )
);

CREATE INDEX IF NOT EXISTS
    external_user_identities_user_id_idx
ON external_user_identities (
    user_id
);

CREATE TABLE IF NOT EXISTS profile_memberships (
    membership_id UUID PRIMARY KEY,

    user_id UUID NOT NULL
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    profile_id UUID NOT NULL
        REFERENCES digital_human_profiles(profile_id)
        ON DELETE CASCADE,

    role TEXT NOT NULL
        CHECK (
            role IN (
                'owner'
            )
        ),

    status TEXT NOT NULL DEFAULT 'active'
        CHECK (
            status IN (
                'active',
                'inactive',
                'revoked'
            )
        ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT profile_memberships_user_profile_unique
        UNIQUE (
            user_id,
            profile_id
        )
);

CREATE INDEX IF NOT EXISTS
    profile_memberships_profile_id_idx
ON profile_memberships (
    profile_id
);

CREATE INDEX IF NOT EXISTS
    profile_memberships_user_active_idx
ON profile_memberships (
    user_id,
    status
);

INSERT INTO schema_migrations (
    version
)
VALUES (
    '011_user_identity_profile_memberships'
);
