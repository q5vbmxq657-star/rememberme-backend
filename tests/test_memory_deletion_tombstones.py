from uuid import UUID, uuid4
from unittest.mock import Mock
import json

import psycopg
import pytest
from fastapi import HTTPException

from test_memory_index_generation_integration import database, service, snapshot
from app.services.pgvector_memory_service import PGVectorStaleIndexError
from app.routes import vector_memory


@pytest.mark.parametrize("writer", ["index", "index_external_memories"])
def test_deleted_memory_cannot_return_from_stale_device_or_interview(database, writer):
    url, (profile, other) = database
    store = service(url)
    store.index(snapshot(profile, "removed", "keep"))
    assert store.delete_memory(profile_id=profile, memory_id=" REMOVED ")["status"] == "deleted"
    store._embed = Mock(side_effect=AssertionError("Must reject before disclosing content"))
    with pytest.raises(PGVectorStaleIndexError):
        getattr(store, writer)(snapshot(profile, "removed"))
    store._embed.assert_not_called()
    assert store.memory_usage(profile)["deleted_memory_ids"] == ["removed"]
    assert store.memory_usage(other)["deleted_memory_ids"] == []
    with pytest.raises(PGVectorStaleIndexError):
        store.update_memory_usage(profile_id=profile, memory_id="removed", included=True, expected_revision=0)


def test_deletion_is_idempotent_and_invalidates_history(database):
    url, (profile, _) = database
    store = service(url)
    store.index(snapshot(profile, "removed"))
    user = uuid4()
    with psycopg.connect(url) as connection:
        connection.execute("INSERT INTO users(user_id) VALUES (%s)", (user,))
        connection.execute("""INSERT INTO memory_conversation_history
            (profile_id,user_id,conversation_id,evidence_version,consent_revision,messages)
            VALUES (%s,%s,%s,'[]',1,'["private historical context"]')""", (profile, user, uuid4()))
    try:
        first = store.delete_memory(profile_id=profile, memory_id="removed")
        version = store.evidence_version(profile)
        assert store.delete_memory(profile_id=profile, memory_id="removed") == first
        assert store.evidence_version(profile) == version
        with psycopg.connect(url) as connection:
            assert connection.execute("SELECT messages FROM memory_conversation_history WHERE profile_id=%s", (profile,)).fetchone()[0] == []
            assert connection.execute("SELECT count(*) FROM memory_embeddings WHERE profile_id=%s", (profile,)).fetchone()[0] == 0
    finally:
        with psycopg.connect(url) as connection:
            connection.execute("DELETE FROM users WHERE user_id=%s", (user,))


@pytest.mark.parametrize("writer", ["index", "index_external_memories"])
def test_deletion_supersedes_inflight_index_writer(database, writer):
    url, (profile, _) = database
    store = service(url)
    store.index(snapshot(profile, "removed"))
    def embed(_):
        store.delete_memory(profile_id=profile, memory_id="removed")
        return [1.0] + [0.0] * 1535
    store._embed = embed
    changed = snapshot(profile, "removed")
    changed.memories[0].summary = "New content requiring embedding"
    with pytest.raises(PGVectorStaleIndexError):
        getattr(store, writer)(changed)


@pytest.mark.parametrize("statement", ["insert", "update"])
def test_database_rejects_direct_deleted_memory_write(database, statement):
    url, (profile, _) = database
    store = service(url)
    store.index(snapshot(profile, "removed", "keep"))
    store.delete_memory(profile_id=profile, memory_id="removed")
    with psycopg.connect(url) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                if statement == "update":
                    connection.execute("UPDATE memory_embeddings SET memory_id='REMOVED' WHERE profile_id=%s AND memory_id='keep'", (profile,))
                else:
                    connection.execute("""INSERT INTO memory_embeddings
                        (profile_id,memory_id,title,summary,type,content,embedding)
                        SELECT profile_id,'removed',title,summary,type,content,embedding
                        FROM memory_embeddings WHERE profile_id=%s AND memory_id='keep'""", (profile,))


def test_delete_route_checks_profile_before_mutation(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(vector_memory, "make_service", factory)
    monkeypatch.setattr(vector_memory, "require_profile_access", Mock(side_effect=HTTPException(404, "Profile not found.")))
    with pytest.raises(HTTPException):
        vector_memory.delete_memory(uuid4(), "memory", principal=object())
    factory.assert_not_called()


def test_delete_route_returns_confirmed_canonical_identifier(monkeypatch):
    profile = uuid4()
    store = Mock()
    store.delete_memory.return_value = {"profile_id": str(profile), "memory_id": "memory", "status": "deleted"}
    authorization = Mock()
    monkeypatch.setattr(vector_memory, "require_profile_access", authorization)
    monkeypatch.setattr(vector_memory, "make_service", lambda: store)
    response = vector_memory.delete_memory(profile, "memory", principal=object())
    assert json.loads(response.body)["status"] == "deleted"
    assert authorization.call_count == 2
    assert response.headers["cache-control"] == "no-store"
