ALTER TABLE apple_purchase_transactions
    ADD COLUMN allowance_next_check_at TIMESTAMPTZ DEFAULT NOW(),
    ADD COLUMN allowance_lease UUID;
CREATE INDEX annual_allowance_due ON apple_purchase_transactions(allowance_next_check_at)
    WHERE cadence='annual' AND revoked_at IS NULL AND allowance_next_check_at IS NOT NULL;
INSERT INTO schema_migrations(version) VALUES ('044_annual_allowance_scheduler');
