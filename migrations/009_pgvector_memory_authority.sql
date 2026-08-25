CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memory_embeddings (
    id BIGSERIAL PRIMARY KEY,

    memory_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,

    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    original_text TEXT,
    type TEXT NOT NULL,

    emotional_tags TEXT[] NOT NULL
        DEFAULT ARRAY[]::TEXT[],

    confidence_score DOUBLE PRECISION NOT NULL
        DEFAULT 0,

    content TEXT NOT NULL,

    embedding vector(1536) NOT NULL,

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT NOW(),

    updated_at TIMESTAMPTZ NOT NULL
        DEFAULT NOW(),

    CONSTRAINT uq_memory_embeddings_profile_memory
        UNIQUE (profile_id, memory_id)
);

ALTER TABLE memory_embeddings
    ADD COLUMN IF NOT EXISTS original_text TEXT;

DO $migration_contract$
DECLARE
    duplicate_count BIGINT;
    actual_columns JSONB;
    expected_columns JSONB;
BEGIN
    SELECT COUNT(*)
    INTO duplicate_count
    FROM (
        SELECT
            profile_id,
            memory_id
        FROM memory_embeddings
        GROUP BY
            profile_id,
            memory_id
        HAVING COUNT(*) > 1
    ) AS duplicates;

    IF duplicate_count > 0 THEN
        RAISE EXCEPTION
            'memory_embeddings contains duplicate profile_id/memory_id identities: %',
            duplicate_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE
            conrelid = 'public.memory_embeddings'::regclass
            AND conname = 'uq_memory_embeddings_profile_memory'
            AND contype = 'u'
    ) THEN
        ALTER TABLE memory_embeddings
            ADD CONSTRAINT uq_memory_embeddings_profile_memory
            UNIQUE (profile_id, memory_id);
    END IF;

    SELECT jsonb_object_agg(
        columns.column_name,
        jsonb_build_object(
            'data_type',
            CASE
                WHEN columns.column_name = 'embedding'
                    THEN format_type(
                        attribute.atttypid,
                        attribute.atttypmod
                    )
                ELSE columns.data_type
            END,
            'nullable',
            columns.is_nullable
        )
    )
    INTO actual_columns
    FROM information_schema.columns AS columns
    JOIN pg_catalog.pg_class AS relation
        ON relation.relname = columns.table_name
    JOIN pg_catalog.pg_namespace AS namespace
        ON namespace.oid = relation.relnamespace
        AND namespace.nspname = columns.table_schema
    JOIN pg_catalog.pg_attribute AS attribute
        ON attribute.attrelid = relation.oid
        AND attribute.attname = columns.column_name
        AND attribute.attnum > 0
        AND NOT attribute.attisdropped
    WHERE
        columns.table_schema = 'public'
        AND columns.table_name = 'memory_embeddings';

    expected_columns := jsonb_build_object(
        'id',
        jsonb_build_object(
            'data_type', 'bigint',
            'nullable', 'NO'
        ),
        'memory_id',
        jsonb_build_object(
            'data_type', 'text',
            'nullable', 'NO'
        ),
        'profile_id',
        jsonb_build_object(
            'data_type', 'text',
            'nullable', 'NO'
        ),
        'title',
        jsonb_build_object(
            'data_type', 'text',
            'nullable', 'NO'
        ),
        'summary',
        jsonb_build_object(
            'data_type', 'text',
            'nullable', 'NO'
        ),
        'original_text',
        jsonb_build_object(
            'data_type', 'text',
            'nullable', 'YES'
        ),
        'type',
        jsonb_build_object(
            'data_type', 'text',
            'nullable', 'NO'
        ),
        'emotional_tags',
        jsonb_build_object(
            'data_type', 'ARRAY',
            'nullable', 'NO'
        ),
        'confidence_score',
        jsonb_build_object(
            'data_type', 'double precision',
            'nullable', 'NO'
        ),
        'content',
        jsonb_build_object(
            'data_type', 'text',
            'nullable', 'NO'
        ),
        'embedding',
        jsonb_build_object(
            'data_type', 'vector(1536)',
            'nullable', 'NO'
        ),
        'created_at',
        jsonb_build_object(
            'data_type', 'timestamp with time zone',
            'nullable', 'NO'
        ),
        'updated_at',
        jsonb_build_object(
            'data_type', 'timestamp with time zone',
            'nullable', 'NO'
        )
    );

    IF actual_columns IS DISTINCT FROM expected_columns THEN
        RAISE EXCEPTION
            'memory_embeddings schema mismatch. actual=%, expected=%',
            actual_columns,
            expected_columns;
    END IF;
END
$migration_contract$;

CREATE INDEX IF NOT EXISTS
    idx_memory_embeddings_profile_id
ON memory_embeddings(profile_id);

CREATE INDEX IF NOT EXISTS
    idx_memory_embeddings_memory_id
ON memory_embeddings(memory_id);

CREATE INDEX IF NOT EXISTS
    idx_memory_embeddings_embedding_hnsw
ON memory_embeddings
USING hnsw (embedding vector_cosine_ops);

DO $index_contract$
DECLARE
    profile_index_definition TEXT;
    memory_index_definition TEXT;
    hnsw_index_definition TEXT;
BEGIN
    SELECT indexdef
    INTO profile_index_definition
    FROM pg_indexes
    WHERE
        schemaname = 'public'
        AND tablename = 'memory_embeddings'
        AND indexname = 'idx_memory_embeddings_profile_id';

    SELECT indexdef
    INTO memory_index_definition
    FROM pg_indexes
    WHERE
        schemaname = 'public'
        AND tablename = 'memory_embeddings'
        AND indexname = 'idx_memory_embeddings_memory_id';

    SELECT indexdef
    INTO hnsw_index_definition
    FROM pg_indexes
    WHERE
        schemaname = 'public'
        AND tablename = 'memory_embeddings'
        AND indexname = 'idx_memory_embeddings_embedding_hnsw';

    IF profile_index_definition IS NULL
        OR profile_index_definition NOT LIKE '%(profile_id)%'
    THEN
        RAISE EXCEPTION
            'Canonical profile_id index is missing or invalid.';
    END IF;

    IF memory_index_definition IS NULL
        OR memory_index_definition NOT LIKE '%(memory_id)%'
    THEN
        RAISE EXCEPTION
            'Canonical memory_id index is missing or invalid.';
    END IF;

    IF hnsw_index_definition IS NULL
        OR hnsw_index_definition NOT LIKE '%USING hnsw%'
        OR hnsw_index_definition NOT LIKE '%vector_cosine_ops%'
    THEN
        RAISE EXCEPTION
            'Canonical HNSW vector_cosine_ops index is missing or invalid.';
    END IF;
END
$index_contract$;

INSERT INTO schema_migrations (
    version
)
VALUES (
    '009_pgvector_memory_authority'
);
