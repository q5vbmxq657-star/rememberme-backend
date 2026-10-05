-- Deleting a Family must not make its subscription reusable for another pool.
ALTER TABLE apple_family_credit_bindings DROP CONSTRAINT apple_family_credit_bindings_family_id_fkey;
ALTER TABLE apple_family_credit_bindings ALTER COLUMN family_id DROP NOT NULL;
ALTER TABLE apple_family_credit_bindings ADD CONSTRAINT apple_family_credit_bindings_family_id_fkey
    FOREIGN KEY (family_id) REFERENCES family_groups(family_id) ON DELETE SET NULL;
INSERT INTO schema_migrations(version) VALUES ('042_family_billing_binding_retention');
