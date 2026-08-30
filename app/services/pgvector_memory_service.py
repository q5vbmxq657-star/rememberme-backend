from __future__ import annotations

import math
import os
from collections.abc import Sequence
from typing import Any

import psycopg
from openai import OpenAI
from psycopg.rows import dict_row

from app.schemas.vector_memory import (
    IndexMemoryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SearchMemoryResult,
    VectorMemoryItem,
)


PGVECTOR_AUTHORITY_VERSION = (
    "009_pgvector_memory_authority"
)

PGVECTOR_EXTENSION_NAME = "vector"
PGVECTOR_EXTENSION_VERSION = "0.8.5"
PGVECTOR_EMBEDDING_DIMENSIONS = 1536

PGVECTOR_REQUIRED_INDEXES = {
    "idx_memory_embeddings_profile_id",
    "idx_memory_embeddings_memory_id",
    "idx_memory_embeddings_embedding_hnsw",
}

PGVECTOR_REQUIRED_UNIQUE_CONSTRAINT = (
    "uq_memory_embeddings_profile_memory"
)


class PGVectorSchemaNotReadyError(
    RuntimeError
):
    pass


class PGVectorMemoryService:
    def __init__(
        self,
        *,
        client: Any | None = None,
        database_url: str | None = None,
        validate_schema: bool = True,
    ) -> None:
        resolved_database_url = (
            database_url
            or os.getenv("DATABASE_URL")
        )

        if not resolved_database_url:
            raise RuntimeError(
                "DATABASE_URL is missing."
            )

        if client is None:
            api_key = os.getenv(
                "OPENAI_API_KEY"
            )

            if not api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY is missing."
                )

            client = OpenAI(
                api_key=api_key
            )

        self.client = client
        self.database_url = resolved_database_url
        self.embedding_model = os.getenv(
            "OPENAI_EMBEDDING_MODEL",
            "text-embedding-3-small",
        )
        self.embedding_dimensions = (
            PGVECTOR_EMBEDDING_DIMENSIONS
        )

        if validate_schema:
            self._assert_schema_ready()

    def index(
        self,
        request: IndexMemoryRequest,
    ) -> dict[str, object]:
        prepared_rows = []

        for memory in request.memories:
            if memory.profile_id != request.profile_id:
                raise ValueError(
                    "Every memory profile_id must "
                    "match the request profile_id."
                )

            content = self._memory_text(
                memory
            )

            embedding = self._embed(
                content
            )

            prepared_rows.append(
                (
                    memory.id,
                    memory.profile_id,
                    memory.title,
                    memory.summary,
                    memory.original_text,
                    memory.type,
                    memory.emotional_tags,
                    float(
                        memory.confidence_score
                    ),
                    content,
                    self._vector_literal(
                        embedding
                    ),
                )
            )

        with psycopg.connect(
            self.database_url
        ) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        DELETE FROM memory_embeddings
                        WHERE profile_id = %s::text
                        """,
                        (
                            request.profile_id,
                        ),
                    )

                    if prepared_rows:
                        cursor.executemany(
                            """
                            INSERT INTO memory_embeddings (
                                memory_id,
                                profile_id,
                                title,
                                summary,
                                original_text,
                                type,
                                emotional_tags,
                                confidence_score,
                                content,
                                embedding
                            )
                            VALUES (
                                %s::text,
                                %s::text,
                                %s::text,
                                %s::text,
                                %s::text,
                                %s::text,
                                %s::text[],
                                %s::double precision,
                                %s::text,
                                %s::vector
                            )
                            """,
                            prepared_rows,
                        )

        return {
            "status": "indexed",
            "backend": "pgvector",
            "profile_id": request.profile_id,
            "count": len(prepared_rows),
        }

    def upsert_external_memory(
        self,
        *,
        memory_id: str,
        profile_id: str,
        title: str,
        summary: str,
        original_text: str,
        memory_type: str,
        emotional_tags: list[str],
        confidence_score: float,
    ) -> None:
        """Index one externally captured memory without replacing local memories."""
        content = "\n".join(
            value.strip()
            for value in (title, summary, original_text)
            if value and value.strip()
        )
        embedding = self._vector_literal(self._embed(content))
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO memory_embeddings (
                            memory_id, profile_id, title, summary, original_text,
                            type, emotional_tags, confidence_score, content, embedding
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (profile_id, memory_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            summary = EXCLUDED.summary,
                            original_text = EXCLUDED.original_text,
                            type = EXCLUDED.type,
                            emotional_tags = EXCLUDED.emotional_tags,
                            confidence_score = EXCLUDED.confidence_score,
                            content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding,
                            updated_at = NOW()
                        """,
                        (
                            memory_id, profile_id, title, summary, original_text,
                            memory_type, emotional_tags, confidence_score, content, embedding,
                        ),
                    )

    def search(
        self,
        request: SearchMemoryRequest,
    ) -> SearchMemoryResponse:
        query_vector = self._vector_literal(
            self._embed(
                request.query
            )
        )

        limit = min(
            100,
            max(
                1,
                request.limit,
            ),
        )

        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        memory_id,
                        title,
                        summary,
                        original_text,
                        type,
                        emotional_tags,
                        confidence_score,
                        1 - (
                            embedding <=> %s::vector
                        ) AS similarity_score
                    FROM memory_embeddings
                    WHERE profile_id = %s::text
                    ORDER BY
                        embedding <=> %s::vector ASC,
                        confidence_score DESC,
                        memory_id ASC
                    LIMIT %s::integer
                    """,
                    (
                        query_vector,
                        request.profile_id,
                        query_vector,
                        limit,
                    ),
                )

                rows = cursor.fetchall()

        return SearchMemoryResponse(
            results=[
                SearchMemoryResult(
                    id=str(
                        row["memory_id"]
                    ),
                    title=str(
                        row["title"]
                    ),
                    summary=str(
                        row["summary"]
                    ),
                    original_text=(
                        str(
                            row[
                                "original_text"
                            ]
                        )
                        if row[
                            "original_text"
                        ] is not None
                        else None
                    ),
                    type=str(
                        row["type"]
                    ),
                    emotional_tags=list(
                        row[
                            "emotional_tags"
                        ]
                        or []
                    ),
                    confidence_score=float(
                        row[
                            "confidence_score"
                        ]
                    ),
                    similarity_score=float(
                        row[
                            "similarity_score"
                        ]
                    ),
                )
                for row in rows
            ]
        )

    def list_profile_memories(
        self,
        *,
        profile_id: str,
        limit: int = 100,
    ) -> list[SearchMemoryResult]:
        """Read profile-scoped evidence without requiring an embedding request."""
        resolved_limit = min(100, max(1, limit))

        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        memory_id,
                        title,
                        summary,
                        original_text,
                        type,
                        emotional_tags,
                        confidence_score
                    FROM memory_embeddings
                    WHERE profile_id = %s::text
                    ORDER BY confidence_score DESC, memory_id ASC
                    LIMIT %s::integer
                    """,
                    (profile_id, resolved_limit),
                )
                rows = cursor.fetchall()

        return [
            SearchMemoryResult(
                id=str(row["memory_id"]),
                title=str(row["title"]),
                summary=str(row["summary"]),
                original_text=(
                    str(row["original_text"])
                    if row["original_text"] is not None
                    else None
                ),
                type=str(row["type"]),
                emotional_tags=list(row["emotional_tags"] or []),
                confidence_score=float(row["confidence_score"]),
                similarity_score=0.0,
            )
            for row in rows
        ]

    def _assert_schema_ready(
        self,
    ) -> None:
        try:
            with psycopg.connect(
                self.database_url,
                autocommit=True,
                row_factory=dict_row,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT
                            to_regclass(
                                'public.schema_migrations'
                            ) IS NOT NULL
                                AS version_table_present,
                            to_regclass(
                                'public.schema_migration_audit'
                            ) IS NOT NULL
                                AS audit_table_present,
                            to_regclass(
                                'public.memory_embeddings'
                            ) IS NOT NULL
                                AS memory_table_present,
                            (
                                SELECT extversion
                                FROM pg_extension
                                WHERE extname = %s::text
                            ) AS extension_version
                        """,
                        (
                            PGVECTOR_EXTENSION_NAME,
                        ),
                    )

                    table_state = cursor.fetchone()

                    if table_state is None:
                        raise PGVectorSchemaNotReadyError(
                            "Could not inspect pgvector "
                            "schema readiness."
                        )

                    initial_failures = []

                    if not table_state[
                        "version_table_present"
                    ]:
                        initial_failures.append(
                            "schema_migrations table missing"
                        )

                    if not table_state[
                        "audit_table_present"
                    ]:
                        initial_failures.append(
                            "schema_migration_audit table missing"
                        )

                    if not table_state[
                        "memory_table_present"
                    ]:
                        initial_failures.append(
                            "memory_embeddings table missing"
                        )

                    if (
                        table_state[
                            "extension_version"
                        ]
                        != PGVECTOR_EXTENSION_VERSION
                    ):
                        initial_failures.append(
                            "pgvector extension version "
                            f"is not {PGVECTOR_EXTENSION_VERSION}"
                        )

                    if initial_failures:
                        raise PGVectorSchemaNotReadyError(
                            "PGVector memory schema is not ready: "
                            + ", ".join(
                                initial_failures
                            )
                            + ". Apply canonical database "
                            "migrations before starting the "
                            "memory runtime."
                        )

                    cursor.execute(
                        """
                        SELECT
                            EXISTS (
                                SELECT 1
                                FROM schema_migrations
                                WHERE version = %s::text
                            ) AS migration_applied,
                            EXISTS (
                                SELECT 1
                                FROM schema_migration_audit
                                WHERE
                                    version = %s::text
                                    AND audit_mode = 'executed'
                            ) AS migration_audited
                        """,
                        (
                            PGVECTOR_AUTHORITY_VERSION,
                            PGVECTOR_AUTHORITY_VERSION,
                        ),
                    )

                    authority = cursor.fetchone()

                    if authority is None:
                        raise PGVectorSchemaNotReadyError(
                            "Could not inspect pgvector "
                            "migration authority."
                        )

                    failures = []

                    if not authority[
                        "migration_applied"
                    ]:
                        failures.append(
                            "migration 009 not applied"
                        )

                    if not authority[
                        "migration_audited"
                    ]:
                        failures.append(
                            "migration 009 not audited"
                        )

                    if failures:
                        raise PGVectorSchemaNotReadyError(
                            "PGVector memory schema is not ready: "
                            + ", ".join(failures)
                            + ". Apply canonical database "
                            "migrations before starting the "
                            "memory runtime."
                        )

                    cursor.execute(
                        """
                        SELECT
                            column_name,
                            CASE
                                WHEN column_name = 'embedding'
                                THEN format_type(
                                    attribute.atttypid,
                                    attribute.atttypmod
                                )
                                ELSE columns.data_type
                            END AS data_type,
                            columns.is_nullable
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
                            AND columns.table_name = 'memory_embeddings'
                        """
                    )

                    actual_columns = {
                        str(
                            row[
                                "column_name"
                            ]
                        ): (
                            str(
                                row[
                                    "data_type"
                                ]
                            ),
                            str(
                                row[
                                    "is_nullable"
                                ]
                            ),
                        )
                        for row in cursor.fetchall()
                    }

                    expected_columns = {
                        "id": (
                            "bigint",
                            "NO",
                        ),
                        "memory_id": (
                            "text",
                            "NO",
                        ),
                        "profile_id": (
                            "text",
                            "NO",
                        ),
                        "title": (
                            "text",
                            "NO",
                        ),
                        "summary": (
                            "text",
                            "NO",
                        ),
                        "original_text": (
                            "text",
                            "YES",
                        ),
                        "type": (
                            "text",
                            "NO",
                        ),
                        "emotional_tags": (
                            "ARRAY",
                            "NO",
                        ),
                        "confidence_score": (
                            "double precision",
                            "NO",
                        ),
                        "content": (
                            "text",
                            "NO",
                        ),
                        "embedding": (
                            "vector(1536)",
                            "NO",
                        ),
                        "created_at": (
                            "timestamp with time zone",
                            "NO",
                        ),
                        "updated_at": (
                            "timestamp with time zone",
                            "NO",
                        ),
                    }

                    if actual_columns != expected_columns:
                        raise PGVectorSchemaNotReadyError(
                            "memory_embeddings does not match "
                            "the canonical migration 009 schema."
                        )

                    cursor.execute(
                        """
                        SELECT
                            indexname,
                            indexdef
                        FROM pg_indexes
                        WHERE
                            schemaname = 'public'
                            AND tablename = 'memory_embeddings'
                        """
                    )

                    indexes = {
                        str(
                            row[
                                "indexname"
                            ]
                        ): str(
                            row[
                                "indexdef"
                            ]
                        )
                        for row in cursor.fetchall()
                    }

                    missing_indexes = (
                        PGVECTOR_REQUIRED_INDEXES
                        - set(indexes)
                    )

                    if missing_indexes:
                        raise PGVectorSchemaNotReadyError(
                            "Canonical pgvector indexes "
                            "are missing: "
                            + ", ".join(
                                sorted(
                                    missing_indexes
                                )
                            )
                        )

                    hnsw_definition = indexes[
                        "idx_memory_embeddings_embedding_hnsw"
                    ]

                    if (
                        "USING hnsw"
                        not in hnsw_definition
                        or "vector_cosine_ops"
                        not in hnsw_definition
                    ):
                        raise PGVectorSchemaNotReadyError(
                            "Canonical HNSW "
                            "vector_cosine_ops index is invalid."
                        )

                    cursor.execute(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_constraint
                            WHERE
                                conrelid =
                                    'public.memory_embeddings'::regclass
                                AND conname = %s::text
                                AND contype = 'u'
                        ) AS unique_identity_present
                        """,
                        (
                            PGVECTOR_REQUIRED_UNIQUE_CONSTRAINT,
                        ),
                    )

                    unique_row = cursor.fetchone()

                    if (
                        unique_row is None
                        or not unique_row[
                            "unique_identity_present"
                        ]
                    ):
                        raise PGVectorSchemaNotReadyError(
                            "Canonical profile-memory identity "
                            "constraint is missing."
                        )

        except PGVectorSchemaNotReadyError:
            raise
        except psycopg.Error as error:
            raise PGVectorSchemaNotReadyError(
                "PGVector memory schema readiness "
                "could not be verified. Apply canonical "
                "database migrations before starting "
                "the memory runtime."
            ) from error

    def _embed(
        self,
        text: str,
    ) -> list[float]:
        response = self.client.embeddings.create(
            model=self.embedding_model,
            input=text,
            dimensions=self.embedding_dimensions,
            encoding_format="float",
        )

        if not response.data:
            raise RuntimeError(
                "Embedding provider returned "
                "no embedding data."
            )

        embedding = [
            float(value)
            for value
            in response.data[0].embedding
        ]

        self._validate_embedding(
            embedding
        )

        return embedding

    def _validate_embedding(
        self,
        embedding: Sequence[float],
    ) -> None:
        if (
            len(embedding)
            != self.embedding_dimensions
        ):
            raise RuntimeError(
                "Expected embedding dimension "
                f"{self.embedding_dimensions}, "
                f"got {len(embedding)}."
            )

        invalid_index = next(
            (
                index
                for index, value
                in enumerate(embedding)
                if not math.isfinite(
                    float(value)
                )
            ),
            None,
        )

        if invalid_index is not None:
            raise RuntimeError(
                "Embedding contains a non-finite "
                f"value at index {invalid_index}."
            )

    def _memory_text(
        self,
        memory: VectorMemoryItem,
    ) -> str:
        original_text = (
            memory.original_text
            or ""
        )

        return "\n".join(
            [
                f"Title: {memory.title}",
                f"Type: {memory.type}",
                (
                    "Original memory text: "
                    f"{original_text}"
                ),
                f"Summary: {memory.summary}",
                (
                    "Emotional tags: "
                    + ", ".join(
                        memory.emotional_tags
                    )
                ),
                (
                    "Confidence: "
                    f"{memory.confidence_score}"
                ),
            ]
        )

    def _vector_literal(
        self,
        embedding: Sequence[float],
    ) -> str:
        self._validate_embedding(
            embedding
        )

        return (
            "["
            + ",".join(
                format(
                    float(value),
                    ".17g",
                )
                for value in embedding
            )
            + "]"
        )
