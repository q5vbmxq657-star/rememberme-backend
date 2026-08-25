ALTER TABLE external_user_identities
ADD COLUMN provider_refresh_token_ciphertext BYTEA,
ADD COLUMN provider_credentials_verified_at TIMESTAMPTZ;

INSERT INTO schema_migrations (
    version
)
VALUES (
    '013_apple_provider_credentials'
);
