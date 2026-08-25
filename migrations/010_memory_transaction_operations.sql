CREATE TABLE IF NOT EXISTS memory_transaction_operations (
    transaction_id UUID NOT NULL,
    operation_type TEXT NOT NULL,

    profile_id UUID NOT NULL
        REFERENCES digital_human_profiles(profile_id)
        ON DELETE CASCADE,

    memory_id TEXT NOT NULL,

    asset_id UUID
        REFERENCES avatar_evidence_assets(asset_id)
        ON DELETE SET NULL,

    ingestion_id UUID,

    modality TEXT NOT NULL,
    status TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,

    result_payload JSONB NOT NULL
        DEFAULT '{}'::jsonb,

    safe_error_code TEXT,

    retry_count INTEGER NOT NULL
        DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT NOW(),

    updated_at TIMESTAMPTZ NOT NULL
        DEFAULT NOW(),

    completed_at TIMESTAMPTZ,

    CONSTRAINT memory_transaction_operations_primary_key
        PRIMARY KEY (
            transaction_id,
            operation_type
        ),

    CONSTRAINT memory_transaction_operation_type_check
        CHECK (
            operation_type IN (
                'upload',
                'ingestion'
            )
        ),

    CONSTRAINT memory_transaction_modality_check
        CHECK (
            modality IN (
                'text',
                'image',
                'voice'
            )
        ),

    CONSTRAINT memory_transaction_status_check
        CHECK (
            status IN (
                'accepted',
                'processing',
                'succeeded',
                'retryable_failed',
                'terminal_failed'
            )
        ),

    CONSTRAINT memory_transaction_memory_id_check
        CHECK (
            LENGTH(BTRIM(memory_id)) > 0
        ),

    CONSTRAINT memory_transaction_fingerprint_check
        CHECK (
            request_fingerprint
                ~ '^[0-9a-f]{64}$'
        ),

    CONSTRAINT memory_transaction_retry_count_check
        CHECK (
            retry_count >= 0
        ),

    CONSTRAINT memory_transaction_ingestion_identity_check
        CHECK (
            (
                operation_type = 'ingestion'
                AND ingestion_id IS NOT NULL
            )
            OR (
                operation_type = 'upload'
                AND ingestion_id IS NULL
            )
        ),

    CONSTRAINT memory_transaction_completion_check
        CHECK (
            (
                status IN (
                    'succeeded',
                    'terminal_failed'
                )
                AND completed_at IS NOT NULL
            )
            OR (
                status IN (
                    'accepted',
                    'processing',
                    'retryable_failed'
                )
                AND completed_at IS NULL
            )
        ),

    CONSTRAINT memory_transaction_result_check
        CHECK (
            status <> 'succeeded'
            OR result_payload <> '{}'::jsonb
        ),

    CONSTRAINT memory_transaction_safe_error_check
        CHECK (
            (
                status IN (
                    'retryable_failed',
                    'terminal_failed'
                )
                AND safe_error_code IS NOT NULL
                AND LENGTH(BTRIM(safe_error_code)) > 0
            )
            OR (
                status IN (
                    'accepted',
                    'processing',
                    'succeeded'
                )
                AND safe_error_code IS NULL
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS
    memory_transaction_ingestion_id_unique
ON memory_transaction_operations (
    ingestion_id
)
WHERE ingestion_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS
    memory_transaction_profile_memory_index
ON memory_transaction_operations (
    profile_id,
    memory_id,
    created_at DESC
);

CREATE INDEX IF NOT EXISTS
    memory_transaction_profile_status_index
ON memory_transaction_operations (
    profile_id,
    status,
    updated_at DESC
);

CREATE INDEX IF NOT EXISTS
    memory_transaction_asset_index
ON memory_transaction_operations (
    asset_id
)
WHERE asset_id IS NOT NULL;

DROP TRIGGER IF EXISTS
    memory_transaction_operations_set_updated_at
ON memory_transaction_operations;

CREATE TRIGGER
    memory_transaction_operations_set_updated_at
BEFORE UPDATE
ON memory_transaction_operations
FOR EACH ROW
EXECUTE FUNCTION rememberme_set_updated_at();

INSERT INTO schema_migrations (
    version
)
VALUES (
    '010_memory_transaction_operations'
);
