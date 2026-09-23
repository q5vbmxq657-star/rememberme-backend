from __future__ import annotations

import ast
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

import pytest

from app.schemas.vector_memory import IndexMemoryRequest, VectorMemoryItem
from app.services.pgvector_memory_service import PGVectorMemoryService, PGVectorInputError, PGVectorStaleIndexError
from app.routes.vector_memory import memory_runtime_error
from app.routes import vector_memory
from app.schemas.vector_memory import MemoryUsageUpdate
from fastapi import HTTPException
from unittest.mock import Mock


MIGRATION_PATH = (
    Path("migrations")
    / "009_pgvector_memory_authority.sql"
)

SERVICE_PATH = (
    Path("app/services")
    / "pgvector_memory_service.py"
)


@pytest.mark.parametrize("update", [False, True])
def test_memory_usage_routes_authorize_before_storage(monkeypatch, update):
    provider = Mock()
    monkeypatch.setattr(vector_memory, "make_service", provider)
    monkeypatch.setattr(vector_memory, "require_profile_access",
                        Mock(side_effect=HTTPException(404, "Profile not found.")))
    with pytest.raises(HTTPException) as error:
        if update:
            vector_memory.update_memory_usage(MemoryUsageUpdate(profile_id=uuid4(),
                memory_id=str(uuid4()), included=False, expected_revision=0), principal=object())
        else:
            vector_memory.memory_usage(str(uuid4()), principal=object())
    assert error.value.status_code == 404
    provider.assert_not_called()


def test_memory_usage_snapshot_is_not_cacheable(monkeypatch):
    profile = str(uuid4())
    service = Mock()
    service.memory_usage.return_value = {"profile_id": profile, "decisions": []}
    monkeypatch.setattr(vector_memory, "make_service", lambda: service)
    monkeypatch.setattr(vector_memory, "require_profile_access", Mock())
    response = vector_memory.memory_usage(profile, principal=object())
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("update", [False, True])
def test_memory_usage_routes_do_not_deliver_after_revocation(monkeypatch, update):
    service = Mock()
    monkeypatch.setattr(vector_memory, "make_service", lambda: service)
    monkeypatch.setattr(vector_memory, "require_profile_access",
                        Mock(side_effect=[None, HTTPException(404, "Profile not found.")]))
    with pytest.raises(HTTPException) as error:
        if update:
            vector_memory.update_memory_usage(MemoryUsageUpdate(profile_id=uuid4(),
                memory_id=str(uuid4()), included=False, expected_revision=0), principal=object())
        else:
            vector_memory.memory_usage(str(uuid4()), principal=object())
    assert error.value.status_code == 404


@pytest.mark.parametrize("invalid_position", [0, 1, 2])
def test_mixed_profile_batch_never_transmits_any_content(monkeypatch, invalid_position):
    memories = [
        VectorMemoryItem(id=str(index), profile_id="profile-a", title="private", summary="private", type="text")
        for index in range(3)
    ]
    memories[invalid_position].profile_id = "profile-b"
    service = PGVectorMemoryService(client=object(), database_url="postgresql://unused", validate_schema=False)
    monkeypatch.setattr(service, "_embed", lambda _: pytest.fail("Invalid batch reached the embedding provider"))
    monkeypatch.setattr("app.services.pgvector_memory_service.psycopg.connect",
                        lambda *_args, **_kwargs: pytest.fail("Invalid batch reached persistence"))
    with pytest.raises(PGVectorInputError):
        service.index(IndexMemoryRequest(profile_id="profile-a", memories=memories))


@pytest.mark.parametrize("identifiers", [["same", "same"], ["valid", ""], ["valid", "  "]])
def test_invalid_identifiers_are_rejected_before_embedding_or_replacement(monkeypatch, identifiers):
    service = PGVectorMemoryService(client=object(), database_url="postgresql://unused", validate_schema=False)
    monkeypatch.setattr(service, "_embed", lambda _: pytest.fail("Invalid batch reached the embedding provider"))
    monkeypatch.setattr("app.services.pgvector_memory_service.psycopg.connect",
                        lambda *_args, **_kwargs: pytest.fail("Invalid batch reached persistence"))
    memories = [VectorMemoryItem(id=identifier, profile_id="profile-a", title="private", summary="private", type="text")
                for identifier in identifiers]
    with pytest.raises(PGVectorInputError):
        service.index(IndexMemoryRequest(profile_id="profile-a", memories=memories))


def test_invalid_batch_response_is_actionable_and_does_not_echo_private_content():
    error = memory_runtime_error("indexing", PGVectorInputError("private memory text"))
    assert error.status_code == 422
    assert "private memory text" not in error.detail
    assert "unique memory IDs" in error.detail


def test_superseded_index_has_a_distinct_sanitized_conflict_response():
    error = memory_runtime_error("indexing", PGVectorStaleIndexError("private content"))
    assert error.status_code == 409
    assert "newer memory update" in error.detail
    assert "private content" not in error.detail


@pytest.mark.parametrize("count", [0, 2])
def test_valid_and_empty_batches_preserve_profile_scoped_atomic_replacement(monkeypatch, count):
    monkeypatch.setattr("app.services.pgvector_memory_service.require_profile_purposes", Mock(return_value=type("Consent", (), {"revision": 1})()))
    executed = []
    inserted = []
    embedded = []
    operation_id = uuid4()

    class Cursor:
        def execute(self, sql, parameters):
            executed.append((sql, parameters))
            self.sql = sql

        def executemany(self, sql, rows):
            inserted.extend(rows)

        def fetchone(self):
            if any(table in self.sql for table in ("FROM digital_human_profile_erasure_requests", "FROM memory_usage_decisions", "FROM memory_deletion_tombstones")):
                return None
            return (1, operation_id)

    class Connection:
        def transaction(self):
            return nullcontext()

        def cursor(self):
            return nullcontext(Cursor())

    service = PGVectorMemoryService(client=object(), database_url="postgresql://unused", validate_schema=False)
    monkeypatch.setattr("app.services.pgvector_memory_service.psycopg.connect",
                        lambda *_args, **_kwargs: nullcontext(Connection()))
    monkeypatch.setattr(service, "_embed", lambda content: embedded.append(content) or [0.0] * 1536)
    monkeypatch.setattr(service, "memory_usage", lambda _: {"decisions": [], "deleted_memory_ids": []})
    # This contract covers replacement; identical-snapshot reuse has separate PG tests.
    monkeypatch.setattr(service, "_reuse_published_snapshot", lambda _: False)
    memories = [VectorMemoryItem(id=str(index), profile_id="profile-a", title="allowed", summary="allowed", type="text")
                for index in range(count)]
    result = service.index(IndexMemoryRequest(profile_id="profile-a", memories=memories))
    assert result["count"] == count
    assert len(embedded) == count
    assert len(executed) == 9 + count
    assert "FROM digital_human_profiles" in executed[0][0]
    assert "FOR UPDATE" in executed[0][0]
    assert "FROM digital_human_profile_erasure_requests" in executed[1][0]
    assert "SELECT generation FROM memory_index_generations" in executed[2][0]
    assert "FROM memory_deletion_tombstones" in executed[3][0]
    assert "FROM memory_usage_decisions" in executed[4][0]
    assert "INSERT INTO memory_index_generations" in executed[5][0]
    assert "FOR UPDATE" in executed[-3][0]
    assert "DELETE FROM memory_embeddings" in executed[-2][0]
    assert "WHERE lower(profile_id) = lower(%s::text)" in executed[-2][0]
    assert executed[-2][1] == ("profile-a",)
    assert "SET published_generation" in executed[-1][0]
    assert len(inserted) == count
    assert all(row[1] == "profile-a" for row in inserted)


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
        "memory.profile_id.lower() != request.profile_id.lower()"
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
