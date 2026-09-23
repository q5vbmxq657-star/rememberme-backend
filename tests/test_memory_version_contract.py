from uuid import UUID
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from tests.test_memory_index_generation_integration import database, service, snapshot
from app.routes import vector_memory
from app.services.pgvector_memory_service import PGVectorStaleIndexError


def test_two_devices_with_same_base_have_exactly_one_winner(database):
    url, (profile, _) = database
    def write(identifier):
        request = snapshot(profile, identifier)
        request.expected_revision = 0
        try:
            service(url).index(request)
            return identifier
        except PGVectorStaleIndexError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["device-a", "device-b"]))
    winners = [value for value in results if value]
    assert len(winners) == 1
    assert [m["id"] for m in service(url).content_snapshot(profile)["memories"]] == winners


def test_stale_device_cannot_replace_newer_snapshot_or_trigger_embeddings(database):
    url, (profile, _) = database
    writer = service(url)
    first = snapshot(profile, "one")
    first.expected_revision = 0
    writer.index(first)
    current = writer.content_snapshot(profile)
    assert current["revision"] == 1
    assert [m["id"] for m in current["memories"]] == ["one"]
    stale = snapshot(profile)
    stale.expected_revision = 0
    writer._embed = Mock(side_effect=AssertionError("Stale content reached the provider"))
    with pytest.raises(PGVectorStaleIndexError):
        writer.index(stale)
    assert writer.content_snapshot(profile) == current


def test_fresh_revision_can_publish_merged_content_and_snapshot_filters_privacy(database):
    url, (profile, other) = database
    writer = service(url)
    first = snapshot(profile, "one")
    first.expected_revision = 0
    writer.index(first)
    second = snapshot(profile, "one", "two")
    second.expected_revision = writer.content_snapshot(profile)["revision"]
    writer.index(second)
    writer.update_memory_usage(profile_id=profile, memory_id="one", included=False, expected_revision=0)
    result = writer.content_snapshot(profile)
    assert [m["id"] for m in result["memories"]] == ["two"]
    assert result["decisions"][0]["included"] is False
    assert writer.content_snapshot(other)["memories"] == []
    writer.delete_memory(profile_id=profile, memory_id="two")
    assert writer.content_snapshot(profile)["memories"] == []
    assert "two" in writer.content_snapshot(profile)["deleted_memory_ids"]


def test_legacy_http_write_requires_update_before_storage(monkeypatch):
    monkeypatch.setattr(vector_memory, "require_profile_access", Mock())
    factory = Mock()
    monkeypatch.setattr(vector_memory, "make_service", factory)
    with pytest.raises(HTTPException) as error:
        vector_memory.index_memories(snapshot("00000000-0000-0000-0000-000000000001"), principal=object())
    assert error.value.status_code == 426
    factory.assert_not_called()


def test_snapshot_authorizes_before_and_after_read_and_disables_cache(monkeypatch):
    profile = UUID("00000000-0000-0000-0000-000000000001")
    backend = Mock()
    backend.content_snapshot.return_value = {"profile_id": str(profile), "revision": 0, "memories": []}
    monkeypatch.setattr(vector_memory, "make_service", lambda: backend)
    authorize = Mock()
    monkeypatch.setattr(vector_memory, "require_profile_access", authorize)
    result = vector_memory.memory_snapshot(profile, principal=object())
    assert result.headers["cache-control"] == "no-store"
    assert authorize.call_count == 2
    authorize.side_effect = [None, HTTPException(404, "Profile not found")]
    with pytest.raises(HTTPException):
        vector_memory.memory_snapshot(profile, principal=object())
