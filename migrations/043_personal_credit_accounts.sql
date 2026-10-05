ALTER TABLE family_credit_entries ALTER COLUMN family_id DROP NOT NULL;
ALTER TABLE family_credit_entries ADD COLUMN account_token UUID REFERENCES billing_accounts(account_token);
ALTER TABLE family_credit_entries ADD CONSTRAINT credit_entry_owner
    CHECK (num_nonnulls(family_id, account_token) = 1);
CREATE INDEX credit_entries_account ON family_credit_entries(account_token,environment)
    WHERE account_token IS NOT NULL;
INSERT INTO schema_migrations(version) VALUES ('043_personal_credit_accounts');
