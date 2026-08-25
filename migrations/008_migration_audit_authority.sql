CREATE TABLE schema_migration_audit (
    version TEXT PRIMARY KEY
        REFERENCES schema_migrations(version)
        ON DELETE RESTRICT,

    source_sha256 TEXT NOT NULL
        CONSTRAINT schema_migration_audit_source_sha256_check
        CHECK (
            source_sha256 ~ '^[0-9a-f]{64}$'
        ),

    execution_sha256 TEXT NOT NULL
        CONSTRAINT schema_migration_audit_execution_sha256_check
        CHECK (
            execution_sha256 ~ '^[0-9a-f]{64}$'
        ),

    normalization_mode TEXT NOT NULL
        CONSTRAINT schema_migration_audit_normalization_mode_check
        CHECK (
            normalization_mode IN (
                'none',
                'legacy_outer_transaction_removed'
            )
        ),

    applied_at TIMESTAMPTZ NOT NULL,

    execution_duration_ms BIGINT NOT NULL
        CONSTRAINT schema_migration_audit_execution_duration_check
        CHECK (
            execution_duration_ms >= 0
        ),

    audit_mode TEXT NOT NULL
        CONSTRAINT schema_migration_audit_audit_mode_check
        CHECK (
            audit_mode IN (
                'executed',
                'historical_bootstrap'
            )
        )
);

INSERT INTO schema_migrations (
    version
)
VALUES (
    '008_migration_audit_authority'
);
