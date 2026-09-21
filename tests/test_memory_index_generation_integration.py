"""Run only against an explicitly supplied, isolated local AP1 PostgreSQL cluster."""

import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4
from unittest.mock import Mock

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest
from fastapi import HTTPException

from app.schemas.vector_memory import IndexMemoryRequest, SearchMemoryRequest, VectorMemoryItem
from app.services.pgvector_memory_service import PGVectorMemoryService, PGVectorStaleIndexError, PGVectorInputError
from app.services.digital_human_profile_repository import DigitalHumanProfileRepository, DigitalHumanProfileRepositoryError, StaleVoiceTrainingError
from app.services.profile_membership_repository import ProfileMembershipRepository
from app.services.profile_erasure_service import ProfileErasureService, ProfileErasureServiceError


@pytest.fixture
def database():
    url = os.getenv("STAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires the isolated local AP1 PostgreSQL database")
    config = conninfo_to_dict(url)
    assert config.get("host", "").startswith("/private/tmp/STAY-AP1-PG-")
    assert config.get("dbname") == "stay_ap1"
    profiles = [str(uuid4()), str(uuid4())]
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.executemany("INSERT INTO digital_human_profiles (profile_id) VALUES (%s::uuid)",
                               [(profile,) for profile in profiles])
            cursor.executemany("""INSERT INTO profile_purpose_consents
                (profile_id, revision, policy_version, purposes)
                VALUES (%s::uuid, 1, 'avatar-consent-v1', ARRAY['memory_context','provider_processing'])""",
                [(profile,) for profile in profiles])
    try:
        yield url, profiles
    finally:
        with psycopg.connect(url) as connection:
            connection.execute("DELETE FROM memory_embeddings WHERE profile_id = ANY(%s)", (profiles,))
            connection.execute("DELETE FROM digital_human_profiles WHERE profile_id = ANY(%s::uuid[])", (profiles,))


def service(url):
    instance = PGVectorMemoryService(client=object(), database_url=url)
    instance._embed = lambda _: [1.0] + [0.0] * 1535
    return instance


def snapshot(profile, *ids):
    return IndexMemoryRequest(profile_id=profile, memories=[
        VectorMemoryItem(id=identifier, profile_id=profile, title=identifier, summary="test evidence", type="text")
        for identifier in ids
    ])


@pytest.mark.parametrize("mutation", ["replacement", "deletion", "erasure"])
@pytest.mark.parametrize("completion", ["ready", "failed"])
def test_late_voice_training_cannot_overwrite_newer_profile_state(database, mutation, completion):
    url, (profile, other) = database
    repository = DigitalHumanProfileRepository(database_url=url)
    profile_id = UUID(profile)
    old_job, new_job = str(uuid4()), str(uuid4())
    repository.set_voice_training(profile_id, provider="elevenlabs", status="submitted", provider_job_id=old_job)
    if mutation == "replacement":
        repository.set_voice_training(profile_id, provider="elevenlabs", status="submitted", provider_job_id=new_job)
    elif mutation == "deletion":
        repository.clear_voice_identity(profile_id, expected_voice_id=None,
            expected_job_id=old_job, expected_provider="elevenlabs")
    else:
        repository.create_profile_erasure_request(request_id=uuid4(), profile_id=profile_id, idempotency_key=str(uuid4()))
    before = repository.require(profile_id)
    with pytest.raises(StaleVoiceTrainingError):
        repository.set_voice_training(profile_id, provider="elevenlabs", status=completion,
            provider_job_id=old_job, expected_job_id=old_job, voice_id="obsolete-voice")
    assert repository.require(profile_id) == before
    assert repository.require(UUID(other)).voice_id is None


def test_current_voice_training_can_publish_its_result(database):
    url, (profile, _) = database
    repository = DigitalHumanProfileRepository(database_url=url)
    profile_id, job = UUID(profile), str(uuid4())
    repository.set_voice_training(profile_id, provider="elevenlabs", status="submitted", provider_job_id=job)
    result = repository.set_voice_training(profile_id, provider="elevenlabs", status="ready",
        provider_job_id=job, expected_job_id=job, voice_id="current-voice")
    assert result.voice_id == "current-voice"
    assert result.voice_training_status == "ready"


def test_old_synthesis_failure_cannot_disable_a_replacement_voice(database):
    url, (profile, _) = database
    repository = DigitalHumanProfileRepository(database_url=url)
    profile_id = UUID(profile)
    repository.set_voice_training(profile_id, provider="elevenlabs", status="ready", voice_id="new-voice")
    with pytest.raises(StaleVoiceTrainingError):
        repository.set_voice_training(profile_id, provider="elevenlabs", status="failed", expected_voice_id="old-voice")
    current = repository.require(profile_id)
    assert current.voice_id == "new-voice"
    assert current.voice_training_status == "ready"


@pytest.mark.parametrize("change", ["voice", "job", "provider"])
def test_voice_deletion_cannot_clear_a_changed_identity(database, change):
    url, (profile, _) = database
    repository = DigitalHumanProfileRepository(database_url=url)
    profile_id, old_job = UUID(profile), str(uuid4())
    repository.set_voice_training(profile_id, provider="elevenlabs", status="ready",
        provider_job_id=old_job, voice_id="old-voice")
    repository.set_voice_training(profile_id,
        provider="other-provider" if change == "provider" else "elevenlabs",
        status="ready", provider_job_id=str(uuid4()) if change == "job" else old_job,
        voice_id="new-voice" if change == "voice" else "old-voice")
    before = repository.require(profile_id)
    with pytest.raises(StaleVoiceTrainingError):
        repository.clear_voice_identity(profile_id, expected_voice_id="old-voice",
            expected_job_id=old_job, expected_provider="elevenlabs")
    assert repository.require(profile_id) == before


@pytest.mark.parametrize("voice_id", [None, "current-voice"])
def test_voice_deletion_clears_only_the_requested_identity(database, voice_id):
    url, (profile, _) = database
    repository = DigitalHumanProfileRepository(database_url=url)
    profile_id, job = UUID(profile), str(uuid4())
    repository.set_voice_training(profile_id, provider="elevenlabs", status="submitted",
        provider_job_id=job, voice_id=voice_id)
    deleted = repository.clear_voice_identity(profile_id, expected_voice_id=voice_id,
        expected_job_id=job, expected_provider="elevenlabs")
    assert deleted.voice_id is None
    assert deleted.voice_training_job_id is None
    assert deleted.voice_provider is None
    assert deleted.voice_training_status == "deleted"


def test_provider_deletion_response_does_not_erase_concurrent_replacement(database, monkeypatch):
    import httpx
    from app.services.elevenlabs_voice_service import ElevenLabsVoiceService, ElevenLabsVoiceConflictError
    url, (profile, _) = database
    repository = DigitalHumanProfileRepository(database_url=url)
    profile_id, job = UUID(profile), str(uuid4())
    repository.set_voice_training(profile_id, provider="elevenlabs", status="ready",
        provider_job_id=job, voice_id="old-voice")

    class ProviderClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def delete(self, url, **kwargs):
            assert url.endswith("/voices/old-voice")
            repository.set_voice_training(profile_id, provider="elevenlabs", status="ready",
                provider_job_id=str(uuid4()), voice_id="new-voice")
            return httpx.Response(204)

    monkeypatch.setattr(httpx, "AsyncClient", ProviderClient)
    instance = ElevenLabsVoiceService.__new__(ElevenLabsVoiceService)
    instance.repository = repository
    instance.api_key = "test-only"
    with pytest.raises(ElevenLabsVoiceConflictError):
        asyncio.run(instance.delete_profile_voice(profile_id=profile_id))
    assert repository.require(profile_id).voice_id == "new-voice"


def test_private_memory_is_durable_across_devices_and_both_index_writers(database):
    url, (profile, other) = database
    first, second = service(url), service(url)
    first.index(snapshot(profile, "private", "allowed"))
    first.index(snapshot(other, "private"))
    result = first.update_memory_usage(profile_id=profile, memory_id="PRIVATE", included=False, expected_revision=0)
    assert result["revision"] == 1 and not result["included"]
    second._embed = Mock(side_effect=AssertionError("Excluded content reached the embedding provider"))
    for write in (second.index, second.index_external_memories):
        with pytest.raises(PGVectorStaleIndexError):
            write(snapshot(profile, "allowed", "private"))
    second._embed.assert_not_called()
    assert [item.id for item in first.list_profile_memories(profile_id=profile)] == ["allowed"]
    assert [item.id for item in first.list_profile_memories(profile_id=other)] == ["private"]
    assert second.memory_usage(profile)["decisions"] == [{"memory_id": "private", "included": False, "revision": 1}]


def test_memory_privacy_works_without_openai_credentials(database, monkeypatch):
    url, (profile, _) = database
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    factory = Mock(side_effect=AssertionError("Privacy must not construct a provider client"))
    monkeypatch.setattr("app.services.pgvector_memory_service.OpenAI", factory)
    instance = PGVectorMemoryService(database_url=url)
    instance.update_memory_usage(profile_id=profile, memory_id="private", included=False, expected_revision=0)
    assert not instance.memory_usage(profile)["decisions"][0]["included"]
    factory.assert_not_called()


@pytest.mark.parametrize("mutation", ["exclude", "replace", "pending"])
def test_retrieved_context_cannot_be_used_after_its_index_changes(database, mutation):
    from app.services.memory_chat_retrieval_service import MemoryChatRetrievalService, require_current_memory_evidence
    url, (profile, other) = database
    instance = service(url)
    instance.index(snapshot(profile, "memory"))
    resolver = MemoryChatRetrievalService(service_factory=lambda: instance)
    evidence = resolver.retrieve(profile_id=profile, user_message="test evidence", recent_messages=[], retrieval_limit=5)
    assert len(evidence) == 1
    require_current_memory_evidence(evidence, profile_id=profile)
    with pytest.raises(PGVectorStaleIndexError):
        require_current_memory_evidence(evidence, profile_id=other)
    if mutation == "exclude":
        instance.update_memory_usage(profile_id=profile, memory_id="memory", included=False, expected_revision=0)
    elif mutation == "replace":
        instance.index(snapshot(profile, "new"))
    else:
        instance._reserve_index_generation(snapshot(profile))
    with pytest.raises(PGVectorStaleIndexError):
        require_current_memory_evidence(evidence, profile_id=profile)


def test_privacy_change_versions_preexisting_profiles_without_index_generation(database):
    url, (profile, _) = database
    instance = service(url)
    assert instance.evidence_version(profile) is None
    instance.update_memory_usage(profile_id=profile, memory_id="memory", included=False, expected_revision=0)
    assert instance.evidence_version(profile) is not None
    with pytest.raises(PGVectorStaleIndexError):
        instance.require_evidence_version(profile, None)


def test_stale_device_cannot_reenable_private_memory(database):
    url, (profile, _) = database
    instance = service(url)
    instance.update_memory_usage(profile_id=profile, memory_id="memory", included=False, expected_revision=0)
    with pytest.raises(PGVectorStaleIndexError):
        service(url).update_memory_usage(profile_id=profile, memory_id="memory", included=True, expected_revision=0)
    assert not instance.memory_usage(profile)["decisions"][0]["included"]
    result = instance.update_memory_usage(profile_id=profile, memory_id="memory", included=True, expected_revision=1)
    assert result["revision"] == 2
    instance.index(snapshot(profile, "memory"))
    assert [item.id for item in instance.list_profile_memories(profile_id=profile)] == ["memory"]


@pytest.mark.parametrize("external", [False, True])
def test_privacy_change_fences_running_full_and_interview_jobs(database, external):
    url, (profile, _) = database
    instance, writer = service(url), service(url)
    instance.index(snapshot(profile, "allowed"))
    entered, release = Event(), Event()
    transmitted = []

    def embed(content):
        transmitted.append(content)
        entered.set()
        assert release.wait(10)
        return [1.0] + [0.0] * 1535

    writer._embed = embed
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            job = workers.submit(writer.index_external_memories if external else writer.index,
                                 snapshot(profile, "first", "private"))
            try:
                assert entered.wait(10)
                instance.update_memory_usage(profile_id=profile, memory_id="private", included=False, expected_revision=0)
            finally:
                release.set()
            with pytest.raises(PGVectorStaleIndexError):
                job.result(timeout=10)
        assert len(transmitted) == 1
        assert all(item.id != "private" for item in instance.list_profile_memories(profile_id=profile))
    finally:
        release.set()


def test_local_legacy_exclusion_initializes_once_and_never_overwrites_newer_decision(database):
    url, (profile, _) = database
    instance = service(url)
    request = snapshot(profile)
    request.excluded_memory_ids = ["private"]
    result = instance.index(request)
    assert result["usage_decisions"] == [{"memory_id": "private", "included": False, "revision": 1}]
    instance.update_memory_usage(profile_id=profile, memory_id="private", included=True, expected_revision=1)
    instance.index(request)
    assert instance.memory_usage(profile)["decisions"] == [{"memory_id": "private", "included": True, "revision": 2}]


def test_privacy_change_never_republishes_failed_snapshot(database):
    url, (profile, _) = database
    instance = service(url)
    instance.index(snapshot(profile, "old"))
    instance._reserve_index_generation(snapshot(profile))
    instance.update_memory_usage(profile_id=profile, memory_id="other", included=False, expected_revision=0)
    assert_hidden(instance, profile)


def test_usage_filter_blocks_restored_excluded_embedding(database):
    url, (profile, _) = database
    instance = service(url)
    instance.index(snapshot(profile, "private"))
    with psycopg.connect(url) as connection:
        connection.execute("""INSERT INTO memory_usage_decisions (profile_id, memory_id, included, revision)
                           VALUES (%s::uuid, 'private', FALSE, 1)""", (profile,))
    assert_hidden(instance, profile)


def test_concurrent_same_revision_privacy_updates_have_only_one_winner(database):
    url, (profile, _) = database
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs = [workers.submit(service(url).update_memory_usage,
                               profile_id=profile, memory_id="memory", included=included, expected_revision=0)
                for included in (False, True)]
        success, conflicts = [], []
        for job in jobs:
            try:
                success.append(job.result(timeout=10))
            except PGVectorStaleIndexError as error:
                conflicts.append(error)
    assert len(success) == len(conflicts) == 1
    assert service(url).memory_usage(profile)["decisions"][0]["revision"] == 1


def assert_hidden(instance, profile):
    assert instance.list_profile_memories(profile_id=profile) == []
    assert instance.search(SearchMemoryRequest(profile_id=profile, query="test evidence")).results == []


@pytest.mark.parametrize("external", [False, True])
def test_erasure_request_fences_running_and_future_memory_jobs(database, external):
    url, (profile, other) = database
    reader = service(url)
    reader.index(snapshot(profile, "before-erasure"))
    reader.index(snapshot(other, "unaffected-profile"))
    pending_service = service(url)
    entered, release = Event(), Event()
    embedded = []
    request_id = uuid4()

    def delayed_embed(content):
        embedded.append(content)
        entered.set()
        assert release.wait(10)
        return [1.0] + [0.0] * 1535

    pending_service._embed = delayed_embed
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            write = pending_service.index_external_memories if external else pending_service.index
            pending = workers.submit(write, snapshot(profile, "first", "must-not-transmit"))
            try:
                assert entered.wait(10)
                DigitalHumanProfileRepository(database_url=url).create_profile_erasure_request(
                    request_id=request_id, profile_id=UUID(profile), idempotency_key=str(uuid4()))
                assert reader.list_profile_memories(profile_id=profile) == []
                with pytest.raises(HTTPException) as denied:
                    reader.search(SearchMemoryRequest(profile_id=profile, query="test evidence"))
                assert denied.value.status_code == 403
                reader._embed = Mock(side_effect=AssertionError("Erasing profile must not transmit content"))
                for future_write in (reader.index, reader.index_external_memories):
                    with pytest.raises(PGVectorStaleIndexError):
                        future_write(snapshot(profile, "new-write"))
                reader._embed.assert_not_called()
                assert [item.id for item in reader.list_profile_memories(profile_id=other)] == ["unaffected-profile"]
            finally:
                release.set()
            with pytest.raises(PGVectorStaleIndexError):
                pending.result(timeout=10)
        assert len(embedded) == 1
        with psycopg.connect(url) as connection:
            assert connection.execute("SELECT count(*) FROM digital_human_profiles WHERE profile_id = %s::uuid", (profile,)).fetchone()[0] == 1
    finally:
        release.set()
        with psycopg.connect(url) as connection:
            connection.execute("DELETE FROM digital_human_profile_erasure_requests WHERE request_id = %s", (request_id,))


@pytest.mark.parametrize("original_deleted", [False, True])
def test_erasure_key_cannot_replay_another_profiles_receipt(database, original_deleted):
    url, (profile, other) = database
    repository = DigitalHumanProfileRepository(database_url=url)
    request_id, key = uuid4(), str(uuid4())
    first = repository.create_profile_erasure_request(
        request_id=request_id, profile_id=UUID(profile), idempotency_key=key)
    try:
        replay = repository.create_profile_erasure_request(
            request_id=uuid4(), profile_id=UUID(profile), idempotency_key=key)
        assert replay["request_id"] == request_id
        if original_deleted:
            repository.delete_profile_graph(profile_id=UUID(profile))
            with psycopg.connect(url) as connection:
                connection.execute("UPDATE digital_human_profile_erasure_requests SET status = 'completed' WHERE request_id = %s", (request_id,))
        with pytest.raises(DigitalHumanProfileRepositoryError):
            repository.create_profile_erasure_request(
                request_id=uuid4(), profile_id=UUID(other), idempotency_key=key)
        with psycopg.connect(url) as connection:
            row = connection.execute("SELECT profile_id, updated_at FROM digital_human_profile_erasure_requests WHERE request_id = %s", (request_id,)).fetchone()
            assert row == (None if original_deleted else UUID(profile), first["updated_at"])
            assert connection.execute("SELECT count(*) FROM digital_human_profile_erasure_requests WHERE profile_id = %s::uuid", (other,)).fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM digital_human_profiles WHERE profile_id = %s::uuid", (other,)).fetchone()[0] == 1
    finally:
        with psycopg.connect(url) as connection:
            connection.execute("DELETE FROM digital_human_profile_erasure_requests WHERE request_id = %s", (request_id,))


@pytest.mark.parametrize("stage", ["database_cleanup", "retryable_failed", "provider_cleanup"])
def test_erasure_recovers_after_graph_commit_without_repeating_provider_cleanup(database, stage):
    url, (profile, _) = database
    repository = DigitalHumanProfileRepository(database_url=url)
    request_id = uuid4()
    repository.create_profile_erasure_request(request_id=request_id,
        profile_id=UUID(profile), idempotency_key=str(uuid4()))
    providers = [Mock(), Mock(), Mock()]
    erasure = ProfileErasureService(repository=repository, media_storage=providers[0],
        avatar_provider=providers[1], voice_service=providers[2])
    try:
        with psycopg.connect(url) as connection:
            connection.execute("""
                UPDATE digital_human_profile_erasure_requests
                SET status = %s, resume_stage = %s
                WHERE request_id = %s
            """, (stage, "database_cleanup" if stage == "retryable_failed" else None, request_id))
        repository.delete_profile_graph(profile_id=UUID(profile))
        request = repository.get_profile_erasure_request(request_id=request_id)
        assert request["profile_id"] is None
        if stage == "provider_cleanup":
            with pytest.raises(ProfileErasureServiceError):
                asyncio.run(erasure._run(request))
        else:
            asyncio.run(erasure._run(request))
            completed = repository.get_profile_erasure_request(request_id=request_id)
            assert completed["status"] == "completed"
            assert completed["completed_at"] is not None
            asyncio.run(erasure._run(completed))
        for provider in providers:
            assert provider.mock_calls == []
    finally:
        with psycopg.connect(url) as connection:
            connection.execute("DELETE FROM digital_human_profile_erasure_requests WHERE request_id = %s", (request_id,))


@pytest.mark.parametrize("change", ["disabled", "deleted", "logout", "expired", "foreign_session", "erasure"])
def test_profile_authorization_rechecks_durable_identity_and_lifecycle(database, change):
    url, (profile, other) = database
    user, other_user, session, other_session, erasure = [uuid4() for _ in range(5)]
    repository = ProfileMembershipRepository(database_url=url)
    with psycopg.connect(url) as connection:
        connection.execute("INSERT INTO users (user_id) VALUES (%s), (%s)", (user, other_user))
        connection.execute("""
            INSERT INTO profile_memberships (membership_id, user_id, profile_id, role)
            VALUES (%s, %s, %s::uuid, 'owner')
        """, (uuid4(), user, profile))
        connection.execute("""
            INSERT INTO user_sessions (session_id, user_id, refresh_token_hash,
                access_expires_at, refresh_expires_at)
            VALUES (%s, %s, %s, NOW() + INTERVAL '1 hour', NOW() + INTERVAL '1 day'),
                   (%s, %s, %s, NOW() + INTERVAL '1 hour', NOW() + INTERVAL '1 day')
        """, (session, user, str(uuid4()), other_session, other_user, str(uuid4())))
    try:
        assert repository.get(user_id=user, profile_id=UUID(profile), session_id=session) is not None
        assert repository.get(user_id=user, profile_id=UUID(other), session_id=session) is None
        with psycopg.connect(url) as connection:
            if change in ("disabled", "deleted"):
                connection.execute("UPDATE users SET status = %s WHERE user_id = %s", (change, user))
            elif change == "logout":
                connection.execute("UPDATE user_sessions SET revoked_at = NOW() WHERE session_id = %s", (session,))
            elif change == "expired":
                connection.execute("UPDATE user_sessions SET access_expires_at = NOW() - INTERVAL '1 second' WHERE session_id = %s", (session,))
            elif change == "erasure":
                connection.execute("""
                    INSERT INTO digital_human_profile_erasure_requests (request_id, profile_id, idempotency_key)
                    VALUES (%s, %s::uuid, %s)
                """, (erasure, profile, str(uuid4())))
        assert repository.get(user_id=user, profile_id=UUID(profile),
            session_id=other_session if change == "foreign_session" else session) is None
    finally:
        with psycopg.connect(url) as connection:
            connection.execute("DELETE FROM digital_human_profile_erasure_requests WHERE request_id = %s", (erasure,))
            connection.execute("DELETE FROM users WHERE user_id IN (%s, %s)", (user, other_user))


def test_slow_old_job_cannot_restore_memories_after_new_empty_snapshot(database):
    url, (profile, other) = database
    reader = service(url)
    reader.index(snapshot(profile, "excluded"))
    reader.index(snapshot(other, "other-profile"))
    old = service(url)
    entered, release = Event(), Event()
    embedded = []

    def delayed_embed(content):
        embedded.append(content)
        entered.set()
        assert release.wait(10), "Test did not release the old job"
        return [1.0] + [0.0] * 1535

    old._embed = delayed_embed
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(old.index, snapshot(profile, "excluded", "second-excluded"))
        try:
            assert entered.wait(10)
            assert_hidden(reader, profile)
            assert [item.id for item in reader.list_profile_memories(profile_id=other)] == ["other-profile"]
            reader.index(snapshot(profile))
        finally:
            release.set()
        with pytest.raises(PGVectorStaleIndexError):
            pending.result(timeout=10)
    assert len(embedded) == 1, "A superseded job must not transmit the next memory"
    assert_hidden(reader, profile)
    with psycopg.connect(url) as connection:
        assert connection.execute(
            "SELECT generation, published_generation FROM memory_index_generations WHERE profile_id = %s::uuid",
            (profile,),
        ).fetchone() == (3, 3)
        assert connection.execute("SELECT count(*) FROM memory_embeddings WHERE profile_id = %s", (profile,)).fetchone()[0] == 0


def test_embedding_failure_keeps_old_evidence_hidden_until_successful_retry(database):
    url, (profile, _) = database
    reader = service(url)
    reader.index(snapshot(profile, "old"))
    failed = service(url)

    def unavailable(_):
        raise RuntimeError("Synthetic embedding outage")

    failed._embed = unavailable
    with pytest.raises(RuntimeError, match="Synthetic"):
        failed.index(snapshot(profile, "new"))
    assert_hidden(reader, profile)
    reader.index(snapshot(profile, "new"))
    assert [item.id for item in reader.list_profile_memories(profile_id=profile)] == ["new"]


def test_failed_publish_rolls_back_rows_without_reexposing_old_evidence(database):
    url, (profile, _) = database
    reader = service(url)
    reader.index(snapshot(profile, "old"))
    failed = service(url)
    failed._vector_literal = lambda _: "[invalid-vector]"
    with pytest.raises(psycopg.Error):
        failed.index(snapshot(profile, "new"))
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT memory_id FROM memory_embeddings WHERE profile_id = %s", (profile,)).fetchall() == [("old",)]
    assert_hidden(reader, profile)
    reader.index(snapshot(profile, "new"))
    assert [item.id for item in reader.list_profile_memories(profile_id=profile)] == ["new"]


@pytest.mark.parametrize("recreate", [False, True])
def test_profile_deletion_removes_generation_and_blocks_late_publish(database, recreate):
    url, (profile, _) = database
    old = service(url)
    entered, release = Event(), Event()

    def delayed_embed(_):
        entered.set()
        assert release.wait(10)
        return [1.0] + [0.0] * 1535

    old._embed = delayed_embed
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(old.index, snapshot(profile, "old"))
        try:
            assert entered.wait(10)
            with psycopg.connect(url) as connection:
                connection.execute("DELETE FROM digital_human_profiles WHERE profile_id = %s::uuid", (profile,))
            if recreate:
                with pytest.raises(psycopg.errors.CheckViolation):
                    with psycopg.connect(url) as connection:
                        connection.execute("INSERT INTO digital_human_profiles (profile_id) VALUES (%s::uuid)", (profile,))
        finally:
            release.set()
        with pytest.raises(PGVectorStaleIndexError):
            pending.result(timeout=10)
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT count(*) FROM memory_index_generations WHERE profile_id = %s::uuid", (profile,)).fetchone()[0] == 0
        rows = connection.execute("SELECT memory_id FROM memory_embeddings WHERE profile_id = %s", (profile,)).fetchall()
        assert rows == []


def test_external_batch_preserves_existing_memories_and_replay_is_idempotent(database):
    url, (profile, other) = database
    instance = service(url)
    instance.index(snapshot(profile, "local"))
    instance.index(snapshot(other, "other"))
    request = snapshot(profile, "answer-1", "answer-2", "answer-3")
    generation = instance.index_external_memories(request)
    assert instance.index_external_memories(request) == generation
    assert {item.id for item in instance.list_profile_memories(profile_id=profile)} == {
        "local", "answer-1", "answer-2", "answer-3"
    }
    assert [item.id for item in instance.list_profile_memories(profile_id=other)] == ["other"]


@pytest.mark.parametrize("failure", ["embedding", "sql"])
def test_external_batch_never_publishes_partial_answers(database, failure):
    url, (profile, _) = database
    instance = service(url)
    instance.index(snapshot(profile, "local"))
    calls = []

    def embedding(content):
        calls.append(content)
        if len(calls) == 2:
            if failure == "embedding":
                raise RuntimeError("Synthetic embedding failure")
        return [1.0] + [0.0] * 1535

    instance._embed = embedding
    original_literal = instance._vector_literal
    if failure == "sql":
        # Bypass the earlier vector validator to exercise an actual SQL rollback.
        instance._vector_literal = lambda value: "[1.0]" if len(calls) == 2 else original_literal(value)
    with pytest.raises(RuntimeError if failure == "embedding" else psycopg.Error):
        instance.index_external_memories(snapshot(profile, "answer-1", "answer-2"))
    assert [item.id for item in instance.list_profile_memories(profile_id=profile)] == ["local"]


def test_external_batch_is_invisible_until_all_embeddings_are_ready(database):
    url, (profile, _) = database
    reader = service(url)
    reader.index(snapshot(profile, "local"))
    writer = service(url)
    entered, release = Event(), Event()
    calls = []

    def embedding(content):
        calls.append(content)
        if len(calls) == 2:
            entered.set()
            assert release.wait(10)
        return [1.0] + [0.0] * 1535

    writer._embed = embedding
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(writer.index_external_memories, snapshot(profile, "answer-1", "answer-2"))
        try:
            assert entered.wait(10)
            assert [item.id for item in reader.list_profile_memories(profile_id=profile)] == ["local"]
        finally:
            release.set()
        pending.result(timeout=10)
    assert len(reader.list_profile_memories(profile_id=profile)) == 3


def test_new_exclusion_blocks_external_publish_and_next_embedding(database):
    url, (profile, _) = database
    reader = service(url)
    reader.index(snapshot(profile, "excluded"))
    writer = service(url)
    entered, release = Event(), Event()
    calls = []

    def embedding(content):
        calls.append(content)
        entered.set()
        assert release.wait(10)
        return [1.0] + [0.0] * 1535

    writer._embed = embedding
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(writer.index_external_memories, snapshot(profile, "excluded", "next"))
        try:
            assert entered.wait(10)
            reader.index(snapshot(profile))
        finally:
            release.set()
        with pytest.raises(PGVectorStaleIndexError):
            pending.result(timeout=10)
    assert len(calls) == 1
    assert_hidden(reader, profile)


def test_pending_full_snapshot_rejects_external_batch_before_provider_request(database):
    url, (profile, _) = database
    instance = service(url)
    instance._reserve_index_generation(snapshot(profile))
    calls = []
    instance._embed = lambda text: calls.append(text)
    with pytest.raises(PGVectorStaleIndexError):
        instance.index_external_memories(snapshot(profile, "answer"))
    assert calls == []


def test_external_rollback_cannot_delete_newer_canonical_memory(database):
    url, (profile, other) = database
    instance = service(url)
    generation = instance.index_external_memories(snapshot(profile, "answer"))
    instance.index(snapshot(other, "answer"))
    instance.index(snapshot(profile, "answer", "new"))
    instance.delete_external_memories(profile_id=profile, memory_ids=["answer"], expected_generation=generation)
    assert {item.id for item in instance.list_profile_memories(profile_id=profile)} == {"answer", "new"}
    assert [item.id for item in instance.list_profile_memories(profile_id=other)] == ["answer"]


def test_external_rollback_removes_only_its_own_published_answers(database):
    url, (profile, _) = database
    instance = service(url)
    instance.index(snapshot(profile, "local"))
    generation = instance.index_external_memories(snapshot(profile, "answer-1", "answer-2"))
    instance.delete_external_memories(profile_id=profile, memory_ids=["answer-1", "answer-2"], expected_generation=generation)
    assert [item.id for item in instance.list_profile_memories(profile_id=profile)] == ["local"]


@pytest.mark.parametrize("recreate", [False, True])
def test_external_batch_cannot_resurrect_deleted_profile(database, recreate):
    url, (profile, _) = database
    writer = service(url)
    entered, release = Event(), Event()

    def embedding(_):
        entered.set()
        assert release.wait(10)
        return [1.0] + [0.0] * 1535

    writer._embed = embedding
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(writer.index_external_memories, snapshot(profile, "old"))
        try:
            assert entered.wait(10)
            with psycopg.connect(url) as connection:
                connection.execute("DELETE FROM digital_human_profiles WHERE profile_id = %s::uuid", (profile,))
            if recreate:
                with pytest.raises(psycopg.errors.CheckViolation):
                    with psycopg.connect(url) as connection:
                        connection.execute("INSERT INTO digital_human_profiles (profile_id) VALUES (%s::uuid)", (profile,))
        finally:
            release.set()
        with pytest.raises(PGVectorStaleIndexError):
            pending.result(timeout=10)
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT memory_id FROM memory_embeddings WHERE profile_id = %s", (profile,)).fetchall() == []


@pytest.mark.parametrize("invalid", ["foreign", "duplicate", "empty"])
def test_external_batch_rejects_all_invalid_input_before_database_or_embeddings(monkeypatch, invalid):
    instance = PGVectorMemoryService(client=object(), database_url="unused", validate_schema=False)
    request = snapshot(str(uuid4()), "valid", "invalid")
    if invalid == "foreign":
        request.memories[1].profile_id = str(uuid4())
    else:
        request.memories[1].id = "valid" if invalid == "duplicate" else " "

    def unexpected(*args, **kwargs):
        pytest.fail("Invalid batches must not touch database or provider")

    monkeypatch.setattr(psycopg, "connect", unexpected)
    instance._embed = unexpected
    with pytest.raises(PGVectorInputError):
        instance.index_external_memories(request)


def test_persona_lookup_rejects_pending_snapshot_but_accepts_published_empty_snapshot(database):
    url, (profile, _) = database
    instance = service(url)
    instance.index(snapshot(profile, "old"))
    instance._reserve_index_generation(snapshot(profile))
    with pytest.raises(PGVectorStaleIndexError):
        instance.list_profile_memories(profile_id=profile, require_published=True)
    instance.index(snapshot(profile))
    assert instance.list_profile_memories(profile_id=profile, require_published=True) == []


def test_uuid_casing_cannot_hide_memories_or_leave_excluded_legacy_rows(database):
    url, (profile, _) = database
    instance = service(url)
    instance.index(snapshot(profile.upper(), "old"))
    # Simulate the uppercase storage produced by earlier iOS clients.
    with psycopg.connect(url) as connection:
        connection.execute("UPDATE memory_embeddings SET profile_id = upper(profile_id) WHERE profile_id = %s", (profile,))
    assert [item.id for item in instance.list_profile_memories(profile_id=profile, require_published=True)] == ["old"]
    assert [item.id for item in instance.search(SearchMemoryRequest(profile_id=profile, query="evidence")).results] == ["old"]
    instance.index(snapshot(profile, "new"))
    assert [item.id for item in instance.list_profile_memories(profile_id=profile.upper())] == ["new"]
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT memory_id FROM memory_embeddings WHERE lower(profile_id) = %s", (profile,)).fetchall() == [("new",)]


def test_profile_erasure_removes_legacy_embeddings_and_is_idempotent(database):
    url, (profile, other) = database
    instance = service(url)
    instance.index(snapshot(profile, "lower", "upper"))
    instance.index(snapshot(other, "other"))
    with psycopg.connect(url) as connection:
        connection.execute("UPDATE memory_embeddings SET profile_id = upper(profile_id) WHERE profile_id = %s AND memory_id = 'upper'", (profile,))
    repository = DigitalHumanProfileRepository(database_url=url)
    erased = repository.delete_profile_graph(profile_id=UUID(profile))
    assert erased["profile_deleted"] == 1
    assert erased["memory_embeddings"] == 2
    assert repository.delete_profile_graph(profile_id=UUID(profile))["memory_embeddings"] == 0
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT count(*) FROM memory_embeddings WHERE lower(profile_id) = %s", (profile,)).fetchone()[0] == 0
    assert [item.id for item in instance.list_profile_memories(profile_id=other)] == ["other"]


def test_profile_erasure_fences_running_index_job(database):
    url, (profile, _) = database
    instance = service(url)
    instance.index(snapshot(profile, "existing"))
    entered, release = Event(), Event()

    def embedding(_):
        entered.set()
        assert release.wait(10)
        return [1.0] + [0.0] * 1535

    instance._embed = embedding
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(instance.index, snapshot(profile, "late"))
        try:
            assert entered.wait(10)
            DigitalHumanProfileRepository(database_url=url).delete_profile_graph(profile_id=UUID(profile))
        finally:
            release.set()
        with pytest.raises(PGVectorStaleIndexError):
            pending.result(timeout=10)
    with psycopg.connect(url) as connection:
        assert connection.execute("SELECT count(*) FROM memory_embeddings WHERE lower(profile_id) = %s", (profile,)).fetchone()[0] == 0
