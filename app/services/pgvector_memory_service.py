from __future__ import annotations
from app.security.purpose_authorization import require_profile_purposes

import math
import json
import os
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

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
PGVECTOR_SUPPORTED_EXTENSION_VERSIONS = frozenset({"0.8.2", PGVECTOR_EXTENSION_VERSION})
PGVECTOR_EMBEDDING_DIMENSIONS = 1536

PGVECTOR_REQUIRED_INDEXES = {
    "idx_memory_embeddings_profile_id",
    "idx_memory_embeddings_profile_id_normalized",
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


class PGVectorInputError(ValueError):
    pass


class PGVectorStaleIndexError(RuntimeError):
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

    @staticmethod
    def _validate_index_request(request: IndexMemoryRequest) -> None:
        memory_ids: set[str] = set()
        excluded_ids = {identifier.strip().lower() for identifier in request.excluded_memory_ids}
        if any(not identifier or len(identifier) > 200 for identifier in excluded_ids):
            raise PGVectorInputError("Invalid excluded memory identifier.")
        # Reject the entire batch before transmitting any content for embeddings.
        for memory in request.memories:
            if memory.profile_id.lower() != request.profile_id.lower():
                raise PGVectorInputError(
                    "Every memory profile_id must "
                    "match the request profile_id."
                )
            if not memory.id.strip() or len(memory.id) > 200 or memory.id.lower() in memory_ids or memory.id.lower() in excluded_ids:
                raise PGVectorInputError(
                    "Memory identifiers must be nonempty and unique within a batch."
                )
            memory_ids.add(memory.id.lower())

    def _prepare_index_rows(self, request: IndexMemoryRequest, generation: tuple[int, UUID]) -> list[tuple]:
        prepared_rows = []
        if not request.memories:
            return prepared_rows
        consent = require_profile_purposes(request.profile_id, {"memory_context"}, database_url=self.database_url)
        for memory in request.memories:
            self._assert_current_index_generation(request.profile_id, generation)
            content = self._memory_text(
                memory
            )

            require_profile_purposes(request.profile_id, {"memory_context"}, expected_revision=consent.revision,
                database_url=self.database_url)
            embedding = self._embed(
                content
            )

            prepared_rows.append(
                (
                    memory.id,
                    memory.profile_id.lower(),
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
                    memory.confirmed_address,
                    json.dumps(memory.sync_metadata.model_dump(mode="json", exclude_none=True)),
                )
            )

        return prepared_rows

    def index(
        self,
        request: IndexMemoryRequest,
    ) -> dict[str, object]:
        self._validate_index_request(request)
        if self._reuse_published_snapshot(request):
            if request.memories:
                require_profile_purposes(request.profile_id, {"memory_context"}, database_url=self.database_url)
            usage = self.memory_usage(request.profile_id)
            return {"status": "indexed", "backend": "pgvector", "profile_id": request.profile_id,
                    "count": len(request.memories), "usage_decisions": usage["decisions"],
                    "deleted_memory_ids": usage["deleted_memory_ids"]}
        generation = self._reserve_index_generation(request)
        prepared_rows = self._prepare_index_rows(request, generation)

        with psycopg.connect(
            self.database_url
        ) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT generation, operation_id FROM memory_index_generations
                        WHERE profile_id = %s::uuid FOR UPDATE
                        """,
                        (request.profile_id,),
                    )
                    current = cursor.fetchone()
                    if current is None or current != generation:
                        raise PGVectorStaleIndexError("Memory index operation was superseded.")
                    cursor.execute(
                        """
                        DELETE FROM memory_embeddings
                        WHERE lower(profile_id) = lower(%s::text)
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
                                embedding,
                                confirmed_address,
                                sync_metadata
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
                                %s::vector,
                                %s::text,
                                %s::jsonb
                            )
                            """,
                            prepared_rows,
                        )

                    cursor.execute(
                        """
                        UPDATE memory_index_generations
                        SET published_generation = %s, updated_at = NOW()
                        WHERE profile_id = %s::uuid AND generation = %s AND operation_id = %s::uuid
                        """,
                        (generation[0], request.profile_id, generation[0], generation[1]),
                    )

        usage = self.memory_usage(request.profile_id)
        return {
            "status": "indexed",
            "backend": "pgvector",
            "profile_id": request.profile_id,
            "count": len(prepared_rows),
            "usage_decisions": usage["decisions"],
            "deleted_memory_ids": usage["deleted_memory_ids"],
        }

    def _reuse_published_snapshot(self, request: IndexMemoryRequest) -> bool:
        """Reuse identical published content without embedding calls or fence churn."""
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                self._require_index_write_allowed(cursor, request.profile_id, exclusive=True)
                self._require_memory_usage_allowed(cursor, request)
                cursor.execute("""SELECT generation, published_generation FROM memory_index_generations
                    WHERE profile_id=%s::uuid FOR UPDATE""", (request.profile_id,))
                state = cursor.fetchone()
                self._check_client_revision(request, int(state[0]) if state else 0)
                if state is None or state[0] != state[1]:
                    return False
                for identifier in {value.strip().lower() for value in request.excluded_memory_ids}:
                    cursor.execute('SELECT included FROM memory_usage_decisions WHERE profile_id=%s::uuid AND memory_id=%s',
                                   (request.profile_id, identifier))
                    if cursor.fetchone() is None:
                        return False
                cursor.execute("""SELECT memory_id,title,summary,original_text,type,emotional_tags,
                    confidence_score,content,confirmed_address,sync_metadata FROM memory_embeddings
                    WHERE lower(profile_id)=lower(%s) ORDER BY memory_id""", (request.profile_id,))
                expected = sorted((memory.id, memory.title, memory.summary, memory.original_text,
                    memory.type, memory.emotional_tags, float(memory.confidence_score),
                    self._memory_text(memory), memory.confirmed_address,
                    memory.sync_metadata.model_dump(mode="json", exclude_none=True)) for memory in request.memories)
                return cursor.fetchall() == expected

    def _reserve_index_generation(self, request: IndexMemoryRequest) -> tuple[int, UUID]:
        # Commit the fence before embedding work. Failed jobs leave old evidence hidden.
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._require_index_write_allowed(cursor, request.profile_id, exclusive=True)
                    cursor.execute("SELECT generation FROM memory_index_generations WHERE profile_id=%s::uuid FOR UPDATE",
                                   (request.profile_id,))
                    state = cursor.fetchone()
                    self._check_client_revision(request, int(state[0]) if state else 0)
                    # Legacy local exclusions may initialize a decision, never overwrite one.
                    for identifier in sorted({value.strip().lower() for value in request.excluded_memory_ids}):
                        cursor.execute(
                            """INSERT INTO memory_usage_decisions (profile_id, memory_id, included, revision)
                            VALUES (%s::uuid, lower(btrim(%s)), FALSE, 1)
                            ON CONFLICT (profile_id, memory_id) DO NOTHING""",
                            (request.profile_id, identifier),
                        )
                    self._require_memory_usage_allowed(cursor, request)
                    cursor.execute(
                        """
                        INSERT INTO memory_index_generations (profile_id, generation, operation_id)
                        VALUES (%s::uuid, 1, %s::uuid)
                        ON CONFLICT (profile_id) DO UPDATE SET
                            generation = memory_index_generations.generation + 1,
                            operation_id = EXCLUDED.operation_id,
                            updated_at = NOW()
                        RETURNING generation, operation_id
                        """,
                        (request.profile_id, uuid4()),
                    )
                    row = cursor.fetchone()
                    return int(row[0]), row[1]

    @staticmethod
    def _check_client_revision(request: IndexMemoryRequest, revision: int) -> None:
        if request.expected_revision is not None and request.expected_revision != revision:
            raise PGVectorStaleIndexError("The memory snapshot changed. Download it before writing.")

    def content_snapshot(self, profile_id: str) -> dict:
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._require_index_write_allowed(cursor, profile_id, exclusive=True)
                    cursor.execute("SELECT generation FROM memory_index_generations WHERE profile_id=%s::uuid FOR UPDATE", (profile_id,))
                    row = cursor.fetchone()
                    revision = int(row[0]) if row else 0
                    cursor.execute("""SELECT memory_id,title,summary,original_text,type,emotional_tags,confidence_score,confirmed_address,sync_metadata
                        FROM memory_embeddings e WHERE lower(e.profile_id)=lower(%s)
                        AND NOT EXISTS (SELECT 1 FROM memory_deletion_tombstones d WHERE d.profile_id=%s::uuid AND d.memory_id=lower(e.memory_id))
                        AND NOT EXISTS (SELECT 1 FROM memory_usage_decisions u WHERE u.profile_id=%s::uuid AND u.memory_id=lower(e.memory_id) AND NOT u.included)
                        ORDER BY memory_id""", (profile_id, profile_id, profile_id))
                    memories = [dict(id=r[0], profile_id=profile_id, title=r[1], summary=r[2], original_text=r[3],
                        type=r[4], emotional_tags=r[5], confidence_score=float(r[6]), confirmed_address=r[7], sync_metadata=r[8]) for r in cursor.fetchall()]
                    cursor.execute("SELECT memory_id,included,revision FROM memory_usage_decisions WHERE profile_id=%s::uuid ORDER BY memory_id", (profile_id,))
                    decisions = [dict(memory_id=r[0], included=r[1], revision=r[2]) for r in cursor.fetchall()]
                    cursor.execute("SELECT memory_id FROM memory_deletion_tombstones WHERE profile_id=%s::uuid ORDER BY memory_id", (profile_id,))
                    deleted = [r[0] for r in cursor.fetchall()]
        return dict(profile_id=profile_id, revision=revision, memories=memories, decisions=decisions, deleted_memory_ids=deleted)

    @staticmethod
    def _require_index_write_allowed(cursor: Any, profile_id: str, *, exclusive: bool = False) -> None:
        # Keep lock order aligned with profile erasure: profile first, generation second.
        cursor.execute(
            "SELECT profile_id FROM digital_human_profiles WHERE profile_id = %s::uuid "
            + ("FOR UPDATE" if exclusive else "FOR SHARE"),
            (profile_id,),
        )
        if cursor.fetchone() is None:
            raise PGVectorStaleIndexError("Memory profile is no longer available.")
        cursor.execute(
            "SELECT request_id FROM digital_human_profile_erasure_requests WHERE profile_id = %s::uuid LIMIT 1",
            (profile_id,),
        )
        if cursor.fetchone() is not None:
            raise PGVectorStaleIndexError("Memory profile is being erased.")

    @staticmethod
    def _require_memory_usage_allowed(cursor: Any, request: IndexMemoryRequest) -> None:
        cursor.execute(
            """SELECT 1 FROM memory_deletion_tombstones
            WHERE profile_id = %s::uuid AND memory_id = ANY(%s) LIMIT 1""",
            (request.profile_id, [item.id.strip().lower() for item in request.memories]),
        )
        if cursor.fetchone() is not None:
            raise PGVectorStaleIndexError("A memory was permanently deleted. Sync before retrying.")
        cursor.execute(
            """SELECT 1 FROM memory_usage_decisions
            WHERE profile_id = %s::uuid AND memory_id = ANY(%s) AND NOT included LIMIT 1""",
            (request.profile_id, [item.id.strip().lower() for item in request.memories]),
        )
        if cursor.fetchone() is not None:
            raise PGVectorStaleIndexError("Memory permissions changed. Sync before retrying.")

    def memory_usage(self, profile_id: str) -> dict:
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                """SELECT memory_id, included, revision FROM memory_usage_decisions
                WHERE profile_id = %s::uuid ORDER BY memory_id""", (profile_id,),
            ).fetchall()
            deleted = connection.execute(
                "SELECT memory_id FROM memory_deletion_tombstones WHERE profile_id=%s::uuid ORDER BY memory_id",
                (profile_id,),
            ).fetchall()
        return {"profile_id": profile_id, "decisions": [
            {"memory_id": row[0], "included": row[1], "revision": row[2]} for row in rows
        ], "deleted_memory_ids": [row[0] for row in deleted]}

    def delete_memory(self, *, profile_id: str, memory_id: str) -> dict:
        identifier = memory_id.strip().lower()
        if not identifier or len(identifier) > 200:
            raise PGVectorInputError("Invalid memory identifier.")
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                self._require_index_write_allowed(cursor, profile_id, exclusive=True)
                cursor.execute("SELECT 1 FROM memory_deletion_tombstones WHERE profile_id=%s::uuid AND memory_id=%s",
                               (profile_id, identifier))
                if cursor.fetchone() is None:
                    # The generation change invalidates in-flight writers and clears server chat history.
                    cursor.execute("""INSERT INTO memory_index_generations
                        (profile_id,generation,published_generation,operation_id) VALUES (%s::uuid,1,1,%s)
                        ON CONFLICT (profile_id) DO UPDATE SET
                        published_generation=CASE WHEN memory_index_generations.published_generation=memory_index_generations.generation
                            THEN memory_index_generations.generation+1 ELSE memory_index_generations.published_generation END,
                        generation=memory_index_generations.generation+1, operation_id=EXCLUDED.operation_id,updated_at=NOW()""",
                        (profile_id, uuid4()))
                    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(lower(%s) || ':' || %s, 027))",
                                   (profile_id, identifier))
                    cursor.execute("INSERT INTO memory_deletion_tombstones(profile_id,memory_id) VALUES (%s::uuid,%s)",
                                   (profile_id, identifier))
                cursor.execute("DELETE FROM memory_embeddings WHERE lower(profile_id)=lower(%s) AND lower(btrim(memory_id))=%s",
                               (profile_id, identifier))
        return {"profile_id": profile_id, "memory_id": identifier, "status": "deleted"}

    def evidence_version(self, profile_id: str) -> tuple | None:
        with psycopg.connect(self.database_url) as connection:
            return connection.execute(
                """SELECT generation, published_generation, operation_id
                FROM memory_index_generations WHERE profile_id = %s::uuid""", (profile_id,),
            ).fetchone()

    def require_evidence_version(self, profile_id: str, expected: tuple | None) -> None:
        if self.evidence_version(profile_id) != expected:
            raise PGVectorStaleIndexError("Memory evidence changed after retrieval.")

    def update_memory_usage(self, *, profile_id: str, memory_id: str, included: bool, expected_revision: int) -> dict:
        identifier = memory_id.strip().lower()
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._require_index_write_allowed(cursor, profile_id, exclusive=True)
                    if included:
                        cursor.execute("SELECT 1 FROM memory_deletion_tombstones WHERE profile_id=%s::uuid AND memory_id=%s",
                                       (profile_id, identifier))
                        if cursor.fetchone() is not None:
                            raise PGVectorStaleIndexError("Deleted memories cannot be included again.")
                    cursor.execute(
                        """SELECT included, revision FROM memory_usage_decisions
                        WHERE profile_id = %s::uuid AND memory_id = %s FOR UPDATE""",
                        (profile_id, identifier),
                    )
                    row = cursor.fetchone()
                    current_included, revision = row if row is not None else (True, 0)
                    if revision != expected_revision:
                        raise PGVectorStaleIndexError("Memory permissions changed on another device.")
                    if row is None or current_included != included:
                        revision += 1
                        cursor.execute(
                            """INSERT INTO memory_usage_decisions (profile_id, memory_id, included, revision)
                            VALUES (%s::uuid, %s, %s, %s)
                            ON CONFLICT (profile_id, memory_id) DO UPDATE SET
                            included = EXCLUDED.included, revision = EXCLUDED.revision, updated_at = NOW()""",
                            (profile_id, identifier, included, revision),
                        )
                        # Invalidate running writers without republishing an already pending index.
                        cursor.execute(
                            """INSERT INTO memory_index_generations
                            (profile_id, generation, published_generation, operation_id)
                            VALUES (%s::uuid, 1, 1, %s)
                            ON CONFLICT (profile_id) DO UPDATE SET
                            published_generation = CASE WHEN memory_index_generations.published_generation = memory_index_generations.generation
                                THEN memory_index_generations.generation + 1 ELSE memory_index_generations.published_generation END,
                            generation = memory_index_generations.generation + 1,
                            operation_id = EXCLUDED.operation_id, updated_at = NOW()""", (profile_id, uuid4()),
                        )
                        if not included:
                            cursor.execute(
                                "DELETE FROM memory_embeddings WHERE lower(profile_id) = lower(%s) AND lower(btrim(memory_id)) = %s",
                                (profile_id, identifier),
                            )
        return {"profile_id": profile_id, "memory_id": identifier, "included": included, "revision": revision}

    def _assert_current_index_generation(self, profile_id: str, generation: tuple[int, UUID]) -> None:
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT generation, operation_id FROM memory_index_generations WHERE profile_id = %s::uuid",
                    (profile_id,),
                )
                current = cursor.fetchone()
                if current is None or current != generation:
                    raise PGVectorStaleIndexError("Memory index operation was superseded.")

    def index_external_memories(self, request: IndexMemoryRequest) -> tuple[int, UUID]:
        """Publish one interview atomically without replacing existing memories."""
        self._validate_index_request(request)
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                self._require_index_write_allowed(cursor, request.profile_id)
                self._require_memory_usage_allowed(cursor, request)
                # Bootstrap the existing fence without superseding a pending full snapshot.
                cursor.execute(
                    """
                    INSERT INTO memory_index_generations
                        (profile_id, generation, operation_id, published_generation)
                    VALUES (%s::uuid, 1, %s::uuid, 1)
                    ON CONFLICT (profile_id) DO NOTHING
                    """,
                    (request.profile_id, uuid4()),
                )
                cursor.execute(
                    """
                    SELECT generation, operation_id FROM memory_index_generations
                    WHERE profile_id = %s::uuid AND generation = published_generation
                    """,
                    (request.profile_id,),
                )
                generation = cursor.fetchone()
                if generation is None:
                    raise PGVectorStaleIndexError("Memory index publication is pending.")

        prepared_rows = self._prepare_index_rows(request, generation)
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._require_index_write_allowed(cursor, request.profile_id)
                    cursor.execute(
                        """
                        SELECT generation, operation_id FROM memory_index_generations
                        WHERE profile_id = %s::uuid AND generation = published_generation
                        FOR UPDATE
                        """,
                        (request.profile_id,),
                    )
                    if cursor.fetchone() != generation:
                        raise PGVectorStaleIndexError("Memory index operation was superseded.")
                    cursor.executemany(
                        """
                        INSERT INTO memory_embeddings (
                            memory_id, profile_id, title, summary, original_text,
                            type, emotional_tags, confidence_score, content, embedding, confirmed_address, sync_metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s::jsonb)
                        ON CONFLICT (profile_id, memory_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            summary = EXCLUDED.summary,
                            original_text = EXCLUDED.original_text,
                            type = EXCLUDED.type,
                            emotional_tags = EXCLUDED.emotional_tags,
                            confidence_score = EXCLUDED.confidence_score,
                            content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding,
                            confirmed_address = EXCLUDED.confirmed_address,
                            sync_metadata = EXCLUDED.sync_metadata,
                            updated_at = NOW()
                        WHERE (memory_embeddings.title, memory_embeddings.summary,
                            memory_embeddings.original_text, memory_embeddings.type,
                            memory_embeddings.emotional_tags, memory_embeddings.confidence_score,
                            memory_embeddings.content, memory_embeddings.embedding,
                            memory_embeddings.confirmed_address, memory_embeddings.sync_metadata)
                        IS DISTINCT FROM (EXCLUDED.title, EXCLUDED.summary,
                            EXCLUDED.original_text, EXCLUDED.type,
                            EXCLUDED.emotional_tags, EXCLUDED.confidence_score,
                            EXCLUDED.content, EXCLUDED.embedding, EXCLUDED.confirmed_address, EXCLUDED.sync_metadata)
                        """,
                        prepared_rows,
                    )
                    if cursor.rowcount > 0:
                        generation = self._advance_external_evidence(cursor, request.profile_id)
        return generation

    @staticmethod
    def _advance_external_evidence(cursor: Any, profile_id: str) -> tuple[int, UUID]:
        # Caller holds the profile and published-generation locks in this transaction.
        cursor.execute("""UPDATE memory_index_generations SET
            generation=generation+1, published_generation=generation+1,
            operation_id=%s, updated_at=NOW()
            WHERE profile_id=%s::uuid RETURNING generation, operation_id""",
            (uuid4(), profile_id))
        return cursor.fetchone()

    def delete_external_memories(
        self,
        *,
        profile_id: str,
        memory_ids: list[str],
        expected_generation: tuple[int, UUID],
    ) -> None:
        """Rollback externally captured memories that never became canonical."""
        if not memory_ids:
            return
        with psycopg.connect(self.database_url) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    self._require_index_write_allowed(cursor, profile_id)
                    cursor.execute(
                        """
                        SELECT generation, operation_id FROM memory_index_generations
                        WHERE profile_id = %s::uuid FOR UPDATE
                        """,
                        (profile_id,),
                    )
                    if cursor.fetchone() != expected_generation:
                        return
                    cursor.execute(
                        """
                        DELETE FROM memory_embeddings
                        WHERE lower(profile_id) = lower(%s)
                          AND memory_id = ANY(%s)
                        """,
                        (profile_id, memory_ids),
                    )
                    if cursor.rowcount > 0:
                        self._advance_external_evidence(cursor, profile_id)

    def search(
        self,
        request: SearchMemoryRequest,
    ) -> SearchMemoryResponse:
        require_profile_purposes(request.profile_id, {"memory_context"}, database_url=self.database_url)
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
                        confirmed_address,
                        1 - (
                            embedding <=> %s::vector
                        ) AS similarity_score
                    FROM memory_embeddings
                    WHERE lower(profile_id) = lower(%s::text)
                      AND NOT EXISTS (
                          SELECT 1 FROM memory_usage_decisions AS usage
                          WHERE usage.profile_id::text = lower(memory_embeddings.profile_id)
                            AND usage.memory_id = lower(btrim(memory_embeddings.memory_id)) AND NOT usage.included
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM memory_index_generations AS fence
                          WHERE fence.profile_id::text = lower(memory_embeddings.profile_id)
                            AND fence.generation <> fence.published_generation
                      )
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
                    confirmed_address=row["confirmed_address"],
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
        require_published: bool = False,
    ) -> list[SearchMemoryResult]:
        """Read profile-scoped evidence without requiring an embedding request."""
        resolved_limit = min(100, max(1, limit))

        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                if require_published:
                    cursor.execute(
                        """
                        SELECT generation, published_generation FROM memory_index_generations
                        WHERE profile_id = %s::uuid FOR SHARE
                        """,
                        (profile_id,),
                    )
                    state = cursor.fetchone()
                    if state is not None and state["generation"] != state["published_generation"]:
                        raise PGVectorStaleIndexError("Memory publication is pending.")
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
                        confirmed_address
                    FROM memory_embeddings
                    WHERE lower(profile_id) = lower(%s::text)
                      AND NOT EXISTS (
                          SELECT 1 FROM memory_usage_decisions AS usage
                          WHERE usage.profile_id::text = lower(memory_embeddings.profile_id)
                            AND usage.memory_id = lower(btrim(memory_embeddings.memory_id)) AND NOT usage.included
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM memory_index_generations AS fence
                          WHERE fence.profile_id::text = lower(memory_embeddings.profile_id)
                            AND fence.generation <> fence.published_generation
                      )
                    ORDER BY confidence_score DESC, memory_id ASC
                    LIMIT %s::integer
                    """,
                    (profile_id, resolved_limit),
                )
                rows = cursor.fetchall()

        return [
            SearchMemoryResult(
                confirmed_address=row["confirmed_address"],
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

    def confirmed_profile_address(self, profile_id: str) -> str | None:
        """Resolve a unique explicit address across the entire eligible profile.

        LIMIT 2 caps output, not the evidence scanned: a second distinct value
        proves a conflict regardless of relevance ranking or retrieval limits.
        """
        require_profile_purposes(profile_id, {"memory_context"}, database_url=self.database_url)
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                """SELECT DISTINCT m.confirmed_address
                FROM memory_embeddings m
                JOIN memory_index_generations g ON g.profile_id::text=lower(m.profile_id)
                WHERE lower(m.profile_id)=lower(%s)
                  AND g.generation=g.published_generation
                  AND m.confirmed_address IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM memory_usage_decisions u
                    WHERE u.profile_id=g.profile_id AND u.memory_id=lower(btrim(m.memory_id)) AND NOT u.included)
                  AND NOT EXISTS (SELECT 1 FROM memory_deletion_tombstones d
                    WHERE d.profile_id=g.profile_id AND d.memory_id=lower(btrim(m.memory_id)))
                  AND NOT EXISTS (SELECT 1 FROM digital_human_profile_erasure_requests e
                    WHERE e.profile_id=g.profile_id)
                LIMIT 2""", (profile_id,),
            ).fetchall()
        return rows[0][0] if len(rows) == 1 else None

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
                            to_regclass('public.memory_index_generations') IS NOT NULL
                                AS generation_table_present,
                            to_regclass('public.memory_usage_decisions') IS NOT NULL
                                AS usage_table_present,
                            to_regclass('public.memory_deletion_tombstones') IS NOT NULL
                                AS deletion_table_present,
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

                    if not table_state["generation_table_present"]:
                        initial_failures.append("memory_index_generations table missing")
                    if not table_state["usage_table_present"]:
                        initial_failures.append("memory_usage_decisions table missing")
                    if not table_state["deletion_table_present"]:
                        initial_failures.append("memory_deletion_tombstones table missing")

                    if (
                        table_state[
                            "extension_version"
                        ]
                        not in PGVECTOR_SUPPORTED_EXTENSION_VERSIONS
                    ):
                        initial_failures.append(
                            "pgvector extension version "
                            f"is not supported (expected {', '.join(sorted(PGVECTOR_SUPPORTED_EXTENSION_VERSIONS))})"
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
                            , EXISTS (
                                SELECT 1 FROM schema_migrations
                                WHERE version = '017_memory_index_generations'
                            ) AND EXISTS (
                                SELECT 1 FROM schema_migration_audit
                                WHERE version = '017_memory_index_generations'
                                  AND audit_mode = 'executed'
                            ) AS generation_migration_ready,
                            EXISTS (
                                SELECT 1 FROM schema_migrations WHERE version = '019_memory_usage_decisions'
                            ) AND EXISTS (
                                SELECT 1 FROM schema_migration_audit
                                WHERE version = '019_memory_usage_decisions' AND audit_mode = 'executed'
                            ) AS usage_migration_ready,
                            EXISTS (
                                SELECT 1 FROM schema_migrations WHERE version = '027_memory_deletion_tombstones'
                            ) AND EXISTS (
                                SELECT 1 FROM schema_migration_audit
                                WHERE version = '027_memory_deletion_tombstones' AND audit_mode = 'executed'
                            ) AS deletion_migration_ready,
                            EXISTS (
                                SELECT 1 FROM schema_migrations WHERE version = '030_memory_confirmed_address'
                            ) AND EXISTS (
                                SELECT 1 FROM schema_migration_audit
                                WHERE version = '030_memory_confirmed_address' AND audit_mode = 'executed'
                            ) AS confirmed_address_migration_ready,
                            EXISTS (SELECT 1 FROM schema_migrations WHERE version = '031_memory_sync_metadata')
                            AND EXISTS (SELECT 1 FROM schema_migration_audit WHERE version = '031_memory_sync_metadata'
                                AND audit_mode = 'executed') AS sync_metadata_migration_ready
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

                    if not authority["generation_migration_ready"]:
                        failures.append("migration 017 not applied and audited")
                    if not authority["usage_migration_ready"]:
                        failures.append("migration 019 not applied and audited")
                    if not authority["deletion_migration_ready"]:
                        failures.append("migration 027 not applied and audited")
                    if not authority["confirmed_address_migration_ready"]:
                        failures.append("migration 030 not applied and audited")
                    if not authority["sync_metadata_migration_ready"]:
                        failures.append("migration 031 not applied and audited")

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
                        "sync_metadata": ("jsonb", "NO"),
                        "confirmed_address": ("text", "YES"),
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
                            "the canonical memory schema (migrations 009, 030 and 031)."
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
        # Privacy reads and revocations must not depend on an embedding provider.
        if self.client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is missing.")
            self.client = OpenAI(api_key=api_key, max_retries=0, timeout=25)
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
