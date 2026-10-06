from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.memory import MemoryChatRequest
from app.schemas.streaming_memory import StreamingMemoryChatRequest
from app.services import conversation_usage as module


@pytest.mark.parametrize("schema", [MemoryChatRequest, StreamingMemoryChatRequest])
def test_voice_cannot_be_claimed_without_call_binding(schema):
    with pytest.raises(ValidationError):
        schema(profile_name="Test", relationship="Self", user_message="Hello", channel="voice")
    with pytest.raises(ValidationError):
        schema(profile_name="Test", relationship="Self", user_message="Hello",
               channel="chat", voice_call_id=uuid4())


@pytest.mark.parametrize("schema", [MemoryChatRequest, StreamingMemoryChatRequest])
@pytest.mark.parametrize("active", [True, False])
def test_voice_admission_never_uses_weekly_chat_counter(monkeypatch, schema, active):
    request = schema(profile_name="Test", relationship="Self", user_message="Hello",
                     channel="voice", voice_call_id=uuid4(), conversation_id=uuid4(), profile_id=str(uuid4()))
    principal = SimpleNamespace(user=SimpleNamespace(user_id=uuid4()), session_id=uuid4())
    db = Mock()
    db.execute.return_value.fetchone.return_value = {"call_id": request.voice_call_id} if active else None
    repository = Mock()
    repository.transaction.return_value.__enter__ = Mock(return_value=db)
    repository.transaction.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(module, "FamilyRepository", lambda: repository)
    chat = Mock(side_effect=AssertionError("Voice must never consume chat quota"))
    usage = module.conversation_usage(principal, request, chat_factory=chat)
    if active:
        usage.reserve()
        usage.finish(completed=True)
    else:
        with pytest.raises(HTTPException) as error:
            usage.reserve()
        assert error.value.status_code == 409
    chat.assert_not_called()
    sql, args = db.execute.call_args.args
    assert "auth_session_id=%s" in sql and "expires_at>NOW()" in sql
    assert args[:5] == (request.voice_call_id, principal.user.user_id, principal.session_id,
                        request.profile_id, request.conversation_id)


def test_legacy_chat_still_requires_chat_admission():
    request = MemoryChatRequest(profile_name="Test", relationship="Self", user_message="Hello")
    chat = Mock()
    principal = object()
    assert module.conversation_usage(principal, request, chat_factory=chat) is chat.return_value
    chat.assert_called_once_with(principal, None)
