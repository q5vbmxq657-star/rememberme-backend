import asyncio
import json
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routes import realtime
from app.schemas.memory import MemoryItem
from app.services.memory_chat_retrieval_service import (
    MemoryEvidence,
    require_current_memory_evidence,
)
from app.services.openai_realtime_service import openai_realtime_service
from app.services.openai_memory_service import OpenAIMemoryService
from app.schemas.memory import MemoryChatRequest
from app.services.pgvector_memory_service import PGVectorStaleIndexError
from test_openai_realtime_durable import route_setup


def memory(address=None):
    return MemoryItem(id="confirmed-memory", title="How she greeted me",
        summary="She called me Honey.", type="story", confirmed_address=address)


@pytest.mark.parametrize("mode", ["voice", "video"])
@pytest.mark.parametrize("address", [None, "Honey"])
def test_call_uses_only_structured_server_address(route_setup, monkeypatch, mode, address):
    repo, vector, provider, principal, row = route_setup
    row["metadata"] = {"mode": mode, "profile_name": "Anna", "language": "en-US"}
    evidence = MemoryEvidence([memory(address)], profile_id=str(row["profile_id"]),
        verify=Mock(), confirmed_address=address)
    realtime.retrieval_service.retrieve.return_value = evidence
    monkeypatch.setattr(realtime, "require_current_memory_evidence", require_current_memory_evidence)
    provider._build_avatar_instructions.side_effect = openai_realtime_service._build_avatar_instructions

    asyncio.run(realtime.connect_realtime_avatar_session(row["session_id"],
        realtime.RealtimeConnectRequest(offer_sdp="v=0\r\n"), principal))

    prompt = provider.create_call.call_args.kwargs["instructions"]
    assert f"confirmed_address\n{json.dumps(address)}\n" in prompt
    assert "Never infer a nickname from prose" in prompt
    assert realtime.retrieval_service.retrieve.call_args.kwargs["profile_id"] == str(row["profile_id"])
    evidence.verify.assert_called()


@pytest.mark.parametrize("reason", ["excluded", "foreign_profile"])
def test_invalidated_address_never_reaches_call_provider(route_setup, monkeypatch, reason):
    repo, vector, provider, principal, row = route_setup
    verify = Mock(side_effect=PGVectorStaleIndexError("Evidence changed")) if reason == "excluded" else Mock()
    evidence = MemoryEvidence([memory("Honey")],
        profile_id="another-profile" if reason == "foreign_profile" else str(row["profile_id"]),
        verify=verify)
    realtime.retrieval_service.retrieve.return_value = evidence
    monkeypatch.setattr(realtime, "require_current_memory_evidence", require_current_memory_evidence)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(realtime.connect_realtime_avatar_session(row["session_id"],
            realtime.RealtimeConnectRequest(offer_sdp="v=0\r\n"), principal))

    assert caught.value.status_code == 409
    provider.create_call.assert_not_awaited()
    repo.begin.assert_not_called()


def test_conflicting_profile_address_cannot_be_reintroduced_by_top_result(route_setup, monkeypatch):
    repo, vector, provider, principal, row = route_setup
    # Global profile resolution found conflicting confirmations; search returned only one.
    evidence = MemoryEvidence([memory("Honey")], profile_id=str(row["profile_id"]),
        verify=Mock(), confirmed_address=None)
    realtime.retrieval_service.retrieve.return_value = evidence
    monkeypatch.setattr(realtime, "require_current_memory_evidence", require_current_memory_evidence)
    provider._build_avatar_instructions.side_effect = openai_realtime_service._build_avatar_instructions

    asyncio.run(realtime.connect_realtime_avatar_session(row["session_id"],
        realtime.RealtimeConnectRequest(offer_sdp="v=0\r\n"), principal))

    prompt = provider.create_call.call_args.kwargs["instructions"]
    assert "confirmed_address\nnull\n" in prompt


def test_nonstream_chat_preserves_authoritative_address_when_limiting_evidence():
    evidence = MemoryEvidence([memory()], profile_id="profile-a", verify=Mock(),
        confirmed_address="Honey")
    request = MemoryChatRequest(profile_id="profile-a", profile_name="Anna",
        relationship="Friend", user_message="Hello").model_copy(update={"memories": evidence})
    service = OpenAIMemoryService.__new__(OpenAIMemoryService)
    service.client = Mock()
    service.orchestration = SimpleNamespace(route=lambda _: SimpleNamespace(
        model="test", fallback_model=None, temperature=0.5, max_output_tokens=100))
    service._call_model = Mock(return_value="Hello Honey.")

    result = service.generate_response(request, authorize=Mock())

    assert result.text == "Hello Honey."
    assert 'confirmed_address:\n"Honey"\n' in service._call_model.call_args.kwargs["system_prompt"]
    service.client.close.assert_called_once()
