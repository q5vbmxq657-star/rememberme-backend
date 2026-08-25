from __future__ import annotations

import ast
from pathlib import Path


MIGRATION_PATH = (
    Path("migrations")
    / "009_pgvector_memory_authority.sql"
)

SERVICE_PATH = (
    Path("app/services")
    / "pgvector_memory_service.py"
)


def test_migration_009_owns_complete_schema():
    source = MIGRATION_PATH.read_text(
        encoding="utf-8"
    )

    assert (
        "CREATE EXTENSION IF NOT EXISTS vector;"
        in source
    )

    assert (
        "CREATE TABLE IF NOT EXISTS memory_embeddings"
        in source
    )

    assert (
        "embedding vector(1536) NOT NULL"
        in source
    )

    assert (
        "UNIQUE (profile_id, memory_id)"
        in source
    )

    assert (
        "USING hnsw (embedding vector_cosine_ops)"
        in source
    )

    assert (
        source.count(
            "INSERT INTO schema_migrations"
        )
        == 1
    )

    assert "BEGIN;" not in source
    assert "COMMIT;" not in source

    assert (
        "INSERT INTO schema_migration_audit"
        not in source
    )


def test_pgvector_service_has_zero_runtime_ddl():
    source = SERVICE_PATH.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source
    )

    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }

    assert "_ensure_schema" not in functions
    assert "_assert_schema_ready" in functions

    for forbidden in (
        "CREATE EXTENSION",
        "CREATE TABLE",
        "ALTER TABLE",
        "CREATE INDEX",
        "DROP TABLE",
    ):
        assert forbidden not in source.upper()


def test_service_freezes_embedding_contract():
    source = SERVICE_PATH.read_text(
        encoding="utf-8"
    )

    assert (
        "PGVECTOR_EMBEDDING_DIMENSIONS = 1536"
        in source
    )

    assert (
        'PGVECTOR_EXTENSION_VERSION = "0.8.5"'
        in source
    )

    assert (
        "dimensions=self.embedding_dimensions"
        in source
    )

    assert (
        'encoding_format="float"'
        in source
    )

    assert "math.isfinite" in source


def test_service_enforces_profile_isolation():
    source = SERVICE_PATH.read_text(
        encoding="utf-8"
    )

    assert (
        "memory.profile_id != request.profile_id"
        in source
    )

    assert (
        "Every memory profile_id must "
        in source
    )


def test_service_uses_canonical_migration_readiness():
    source = SERVICE_PATH.read_text(
        encoding="utf-8"
    )

    assert (
        '"009_pgvector_memory_authority"'
        in source
    )

    assert (
        "PGVectorSchemaNotReadyError"
        in source
    )

    assert (
        source.count(
            "_assert_schema_ready"
        )
        == 2
    )
