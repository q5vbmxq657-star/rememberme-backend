CREATE TABLE billing_accounts (
    account_token UUID PRIMARY KEY,
    user_id UUID UNIQUE REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE apple_subscription_ownership (
    environment TEXT NOT NULL CHECK (environment IN ('Production','Sandbox')),
    original_transaction_id TEXT NOT NULL CHECK (original_transaction_id ~ '^[0-9]{1,128}$'),
    account_token UUID NOT NULL REFERENCES billing_accounts(account_token),
    PRIMARY KEY (environment, original_transaction_id)
);
CREATE TABLE apple_purchase_transactions (
    environment TEXT NOT NULL,
    transaction_id TEXT NOT NULL CHECK (transaction_id ~ '^[0-9]{1,128}$'),
    original_transaction_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('plus','family')),
    cadence TEXT NOT NULL CHECK (cadence IN ('monthly','annual')),
    paid_from TIMESTAMPTZ NOT NULL,
    paid_until TIMESTAMPTZ NOT NULL CHECK (paid_until > paid_from),
    revoked_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (environment, transaction_id),
    FOREIGN KEY (environment,original_transaction_id)
        REFERENCES apple_subscription_ownership(environment,original_transaction_id)
);
CREATE INDEX apple_purchase_transactions_subscription
    ON apple_purchase_transactions(environment,original_transaction_id);
CREATE TABLE apple_purchase_revocations (
    environment TEXT NOT NULL CHECK (environment IN ('Production','Sandbox')),
    transaction_id TEXT NOT NULL CHECK (transaction_id ~ '^[0-9]{1,128}$'),
    revoked_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (environment, transaction_id)
);
INSERT INTO schema_migrations(version) VALUES ('039_apple_purchase_registry');
