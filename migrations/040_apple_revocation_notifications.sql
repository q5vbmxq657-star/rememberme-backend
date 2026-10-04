CREATE TABLE apple_revocation_notifications (
    environment TEXT NOT NULL CHECK (environment IN ('Production','Sandbox')),
    notification_id UUID NOT NULL,
    transaction_id TEXT NOT NULL CHECK (transaction_id ~ '^[0-9]{1,128}$'),
    revoked_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(environment,notification_id)
);
INSERT INTO schema_migrations(version) VALUES ('040_apple_revocation_notifications');
