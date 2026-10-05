ALTER TABLE family_credit_entries DROP CONSTRAINT family_credit_entries_kind_check;
ALTER TABLE family_credit_entries DROP CONSTRAINT family_credit_entries_check;
ALTER TABLE family_credit_entries ADD COLUMN refund_of UUID UNIQUE
    REFERENCES family_credit_entries(entry_id);
ALTER TABLE family_credit_entries ADD COLUMN environment TEXT NOT NULL DEFAULT 'Production'
    CHECK (environment IN ('Production', 'Sandbox'));
ALTER TABLE family_credit_entries ADD CONSTRAINT family_credit_entry_kind
    CHECK (kind IN ('grant', 'usage', 'refund'));
ALTER TABLE family_credit_entries ADD CONSTRAINT family_credit_entry_sign
    CHECK ((kind = 'grant' AND units > 0 AND refund_of IS NULL)
        OR (kind = 'usage' AND units < 0 AND refund_of IS NULL)
        OR (kind = 'refund' AND units < 0 AND refund_of IS NOT NULL));

CREATE TABLE apple_family_credit_bindings (
    environment TEXT NOT NULL,
    original_transaction_id TEXT NOT NULL,
    family_id UUID NOT NULL REFERENCES family_groups(family_id) ON DELETE CASCADE,
    PRIMARY KEY (environment, original_transaction_id),
    FOREIGN KEY (environment, original_transaction_id)
        REFERENCES apple_subscription_ownership(environment, original_transaction_id)
);
CREATE TABLE apple_family_credit_allocations (
    environment TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    entry_id UUID NOT NULL UNIQUE REFERENCES family_credit_entries(entry_id) ON DELETE CASCADE,
    PRIMARY KEY (environment, transaction_id, entry_id),
    FOREIGN KEY (environment, transaction_id)
        REFERENCES apple_purchase_transactions(environment, transaction_id)
);
INSERT INTO schema_migrations(version) VALUES ('041_family_subscription_fulfillment');
