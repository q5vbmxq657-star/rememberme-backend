import os
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.schemas.memory import MemoryChatRequest
from app.schemas.profile_consent import CONSENT_POLICY_VERSION
from app.services.memory_conversation_history import (
    MemoryConversationHistoryRepository, MemoryConversationHistoryService,
)
from app.services.pgvector_memory_service import PGVectorStaleIndexError


@pytest.fixture
def history():
    url = os.getenv("STAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires isolated AP1 PostgreSQL")
    config = conninfo_to_dict(url)
    assert config.get("host", "").startswith("/private/tmp/STAY-AP1-PG-")
    assert config.get("dbname") == "stay_ap1"
    profiles, users = [uuid4(), uuid4()], [uuid4(), uuid4()]
    with psycopg.connect(url) as connection:
        for user in users:
            connection.execute("INSERT INTO users(user_id) VALUES (%s)", (user,))
        for profile in profiles:
            connection.execute("INSERT INTO digital_human_profiles(profile_id) VALUES (%s)", (profile,))
            connection.execute("INSERT INTO profile_purpose_consents(profile_id,revision,policy_version,purposes) VALUES (%s,1,%s,%s)",
                               (profile, CONSENT_POLICY_VERSION, ["memory_context", "provider_processing"]))
    yield MemoryConversationHistoryRepository(url), profiles, users
    with psycopg.connect(url) as connection:
        connection.execute("DELETE FROM digital_human_profiles WHERE profile_id=ANY(%s)", (profiles,))
        connection.execute("DELETE FROM users WHERE user_id=ANY(%s)", (users,))


def load(repo, profile, user, **kwargs):
    return repo.load(profile_id=profile, user_id=user, consent_revision=kwargs.pop("consent_revision", 1), **kwargs)


def test_persisted_continuity_is_scoped_to_user_profile_and_conversation(history):
    repo, profiles, users = history
    context = load(repo, profiles[0], users[0])
    repo.append(context, user_message="My nickname is Honey", assistant_message="Hello Honey")
    assert load(repo, profiles[0], users[0]).messages == ("User: My nickname is Honey", "Assistant: Hello Honey")
    assert not load(repo, profiles[1], users[0]).messages
    assert not load(repo, profiles[0], users[1]).messages
    assert not load(repo, profiles[0], users[0], conversation_id=uuid4()).messages


@pytest.mark.parametrize("change", ["consent", "memory"])
def test_revision_change_purges_history_and_blocks_late_completion(history, change):
    repo, profiles, users = history
    initial = load(repo, profiles[0], users[0])
    repo.append(initial, user_message="private detail", assistant_message="private answer")
    context = load(repo, profiles[0], users[0])
    with psycopg.connect(repo.database_url) as connection:
        if change == "consent":
            connection.execute("UPDATE profile_purpose_consents SET revision=2 WHERE profile_id=%s", (profiles[0],))
        else:
            connection.execute("INSERT INTO memory_index_generations(profile_id,generation,published_generation,operation_id) VALUES (%s,1,1,%s)",
                               (profiles[0], uuid4()))
        assert connection.execute("SELECT messages FROM memory_conversation_history WHERE profile_id=%s", (profiles[0],)).fetchone()[0] == []
    with pytest.raises(PGVectorStaleIndexError):
        repo.require_current(context)
    with pytest.raises(PGVectorStaleIndexError):
        repo.append(context, user_message="late", assistant_message="stale")
    fresh = load(repo, profiles[0], users[0], consent_revision=2 if change == "consent" else 1)
    assert fresh.reset and not fresh.messages
    repo.append(fresh, user_message="new", assistant_message="fresh")


def test_concurrent_turn_cannot_overwrite_history(history):
    repo, profiles, users = history
    context = load(repo, profiles[0], users[0])
    repo.append(context, user_message="first", assistant_message="answer")
    with pytest.raises(PGVectorStaleIndexError):
        repo.append(context, user_message="second", assistant_message="late answer")


def test_profile_deletion_cascades_history(history):
    repo, profiles, users = history
    repo.append(load(repo, profiles[0], users[0]), user_message="hello", assistant_message="hi")
    with psycopg.connect(repo.database_url) as connection:
        connection.execute("DELETE FROM digital_human_profiles WHERE profile_id=%s", (profiles[0],))
        assert connection.execute("SELECT count(*) FROM memory_conversation_history WHERE profile_id=%s", (profiles[0],)).fetchone()[0] == 0


def test_client_context_never_reaches_retrieval_or_provider_request(monkeypatch):
    import app.services.memory_conversation_history as module
    for name in ("require_profile_access", "require_current_memory_evidence"):
        monkeypatch.setattr(module, name, Mock())
    monkeypatch.setattr(module, "require_profile_purposes", Mock(return_value=SimpleNamespace(revision=7)))
    repository = Mock()
    repository.load.return_value = SimpleNamespace(messages=("User: trusted history",), consent_revision=7)
    retrieval = Mock()
    retrieval.retrieve.return_value = []
    request = MemoryChatRequest(profile_id=str(uuid4()), profile_name="Person", relationship="friend",
        user_message="hello", persona_context="EXCLUDED BIOGRAPHY", recent_messages=["EXCLUDED SECRET"])
    enriched, context, authorize = MemoryConversationHistoryService(repository).prepare(request,
        principal=SimpleNamespace(user=SimpleNamespace(user_id=uuid4())), retrieval_service=retrieval)
    assert retrieval.retrieve.call_args.kwargs["recent_messages"] == ("User: trusted history",)
    assert enriched.recent_messages == ["User: trusted history"]
    assert "EXCLUDED" not in enriched.model_dump_json()
    assert request.recent_messages == ["EXCLUDED SECRET"]
    authorize()
    repository.require_current.assert_called_with(context)


@pytest.mark.parametrize("ending", ["done", "error", "disconnect", "stale"])
def test_stream_persists_only_completed_authorized_turns(ending):
    repository = Mock()
    repository.append.return_value = 3
    history = MemoryConversationHistoryService(repository)
    context = SimpleNamespace(conversation_id=uuid4(), reset=True)
    request = SimpleNamespace(user_message="hello")
    closed = []
    def source():
        try:
            yield 'event: metadata\ndata: {"status":"started"}\n\n'
            yield 'event: delta\ndata: {"text":"Hello"}\n\n'
            yield f'event: {"error" if ending == "error" else "done"}\ndata: {{}}\n\n'
        finally:
            closed.append(True)
    authorize = Mock()
    events = history.stream_events(source(), context=context, request=request, authorize=authorize)
    assert '"context_reset": true' in next(events)
    assert "Hello" in next(events)
    if ending == "disconnect":
        events.close()
    elif ending == "stale":
        authorize.side_effect = PGVectorStaleIndexError("changed")
        with pytest.raises(PGVectorStaleIndexError):
            next(events)
    else:
        tail = list(events)
        assert len(tail) == 1
    assert closed == [True]
    if ending == "done":
        repository.append.assert_called_once_with(context, user_message="hello", assistant_message="Hello")
        assert '"conversation_revision": 3' in tail[0]
    else:
        repository.append.assert_not_called()


@pytest.mark.parametrize("blocked", ["revoked", "pending"])
def test_history_cannot_load_without_current_authority(history, blocked):
    repo, profiles, users = history
    with psycopg.connect(repo.database_url) as connection:
        if blocked == "revoked":
            connection.execute("UPDATE profile_purpose_consents SET purposes='{}' WHERE profile_id=%s", (profiles[0],))
        else:
            connection.execute("INSERT INTO memory_index_generations(profile_id,generation,published_generation,operation_id) VALUES (%s,1,0,%s)",
                               (profiles[0], uuid4()))
    with pytest.raises(PGVectorStaleIndexError):
        load(repo, profiles[0], users[0])
