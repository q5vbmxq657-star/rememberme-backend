ALTER TABLE family_credit_reservations ALTER COLUMN family_id DROP NOT NULL;
ALTER TABLE family_credit_reservations ADD COLUMN account_token UUID REFERENCES billing_accounts(account_token);
ALTER TABLE family_credit_reservations ADD CONSTRAINT credit_reservation_owner
    CHECK (num_nonnulls(family_id, account_token) = 1);
CREATE INDEX credit_reservations_account_open ON family_credit_reservations(account_token)
    WHERE settled_at IS NULL AND account_token IS NOT NULL;
INSERT INTO schema_migrations(version) VALUES ('046_personal_call_reservations');
