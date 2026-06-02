import os
from typing import List
from openai import OpenAI
import psycopg
from psycopg.rows import dict_row

from app.schemas.vector_memory import (
    IndexMemoryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SearchMemoryResult,
    VectorMemoryItem,
)


class PGVectorMemoryService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        database_url = os.getenv("DATABASE_URL")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        if not database_url:
            raise RuntimeError("DATABASE_URL is missing.")

        self.client = OpenAI(api_key=api_key)
        self.database_url = database_url
        self.embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        self.embedding_dimensions = 1536

        self._ensure_schema()

    def index(self, request: IndexMemoryRequest):
        self._ensure_schema()

        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM memory_embeddings WHERE profile_id = %s;",
                    (request.profile_id,)
                )

                for memory in request.memories:
                    content = self._memory_text(memory)
                    embedding = self._embed(content)
                    embedding_literal = self._vector_literal(embedding)

                    cursor.execute(
                        """
                        INSERT INTO memory_embeddings (
                            memory_id,
                            profile_id,
                            title,
                            summary,
                            type,
                            emotional_tags,
                            confidence_score,
                            content,
                            embedding
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector);
                        """,
                        (
                            memory.id,
                            memory.profile_id,
                            memory.title,
                            memory.summary,
                            memory.type,
                            memory.emotional_tags,
                            float(memory.confidence_score),
                            content,
                            embedding_literal,
                        )
                    )

            connection.commit()

        return {
            "status": "indexed",
            "backend": "pgvector",
            "profile_id": request.profile_id,
            "count": len(request.memories)
        }

    def search(self, request: SearchMemoryRequest) -> SearchMemoryResponse:
        self._ensure_schema()

        query_embedding = self._embed(request.query)
        query_vector = self._vector_literal(query_embedding)

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        memory_id,
                        title,
                        summary,
                        type,
                        emotional_tags,
                        confidence_score,
                        1 - (embedding <=> %s::vector) AS similarity_score
                    FROM memory_embeddings
                    WHERE profile_id = %s
                    ORDER BY
                        embedding <=> %s::vector ASC,
                        confidence_score DESC
                    LIMIT %s;
                    """,
                    (
                        query_vector,
                        request.profile_id,
                        query_vector,
                        max(1, request.limit),
                    )
                )

                rows = cursor.fetchall()

        return SearchMemoryResponse(
            results=[
                SearchMemoryResult(
                    id=row["memory_id"],
                    title=row["title"],
                    summary=row["summary"],
                    type=row["type"],
                    emotional_tags=row["emotional_tags"] or [],
                    confidence_score=float(row["confidence_score"]),
                    similarity_score=float(row["similarity_score"]),
                )
                for row in rows
            ]
        )

    def _ensure_schema(self):
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")

                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_embeddings (
                        id BIGSERIAL PRIMARY KEY,
                        memory_id TEXT NOT NULL,
                        profile_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        type TEXT NOT NULL,
                        emotional_tags TEXT[] NOT NULL DEFAULT '{}',
                        confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0,
                        content TEXT NOT NULL,
                        embedding vector(1536) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )

                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_memory_embeddings_profile_id
                    ON memory_embeddings(profile_id);
                    """
                )

                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_memory_embeddings_memory_id
                    ON memory_embeddings(memory_id);
                    """
                )

                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_memory_embeddings_embedding_hnsw
                    ON memory_embeddings
                    USING hnsw (embedding vector_cosine_ops);
                    """
                )

            connection.commit()

    def _embed(self, text: str) -> List[float]:
        response = self.client.embeddings.create(
            model=self.embedding_model,
            input=text
        )

        return response.data[0].embedding

    def _memory_text(self, memory: VectorMemoryItem) -> str:
        return "\n".join([
            f"Title: {memory.title}",
            f"Type: {memory.type}",
            f"Summary: {memory.summary}",
            f"Emotional tags: {', '.join(memory.emotional_tags)}",
            f"Confidence: {memory.confidence_score}",
        ])

    def _vector_literal(self, embedding: List[float]) -> str:
        if len(embedding) != self.embedding_dimensions:
            raise RuntimeError(
                f"Expected embedding dimension {self.embedding_dimensions}, got {len(embedding)}."
            )

        return "[" + ",".join(str(float(value)) for value in embedding) + "]"
