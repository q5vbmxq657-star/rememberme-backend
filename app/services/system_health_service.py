import os
import psycopg
from openai import OpenAI


class SystemHealthService:
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        self.vector_backend = os.getenv("VECTOR_MEMORY_BACKEND", "json")

    def check(self):
        return {
            "status": "ok",
            "backend": {
                "vector_memory_backend": self.vector_backend,
                "database_configured": self.database_url is not None,
                "openai_configured": self.openai_api_key is not None,
                "embedding_model": self.embedding_model,
            },
            "database": self._check_database(),
            "openai": self._check_openai(),
        }

    def _check_database(self):
        if not self.database_url:
            return {
                "status": "missing",
                "detail": "DATABASE_URL is not configured."
            }

        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()

                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_extension
                        WHERE extname = 'vector'
                    );
                    """
                )
                vector_enabled = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_name = 'memory_embeddings'
                    );
                    """
                )
                table_exists = cursor.fetchone()[0]

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM memory_embeddings;
                    """
                )
                memory_embedding_count = cursor.fetchone()[0] if table_exists else 0

        return {
            "status": "ok",
            "pgvector_enabled": vector_enabled,
            "memory_embeddings_table_exists": table_exists,
            "memory_embedding_count": memory_embedding_count,
        }

    def _check_openai(self):
        if not self.openai_api_key:
            return {
                "status": "missing",
                "detail": "OPENAI_API_KEY is not configured."
            }

        client = OpenAI(api_key=self.openai_api_key)

        embedding = client.embeddings.create(
            model=self.embedding_model,
            input="health check"
        )

        dimensions = len(embedding.data[0].embedding)

        return {
            "status": "ok",
            "embedding_model": self.embedding_model,
            "embedding_dimensions": dimensions,
        }
