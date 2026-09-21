import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from livekit import api

from app.routes import avatar_runtime
from app.schemas.avatar_runtime import AvatarRuntimeSessionResponse
from app.services.avatar_provider_service import AvatarProviderService
from app.services.avatar_runtime_session_service import (
    AvatarRuntimeSessionService, AvatarRuntimeProviderUnavailableError,
    AvatarRuntimeSessionNotFoundError, StoredRuntimeSession,
)
from app.services.avatar_runtime_tavus_adapter import (
    AvatarRuntimeTavusAdapter, TavusRuntimeHandle, TavusRuntimeConnectionError,
)


def runtime_service(*, expired=False):
    service = AvatarRuntimeSessionService.__new__(AvatarRuntimeSessionService)
    service._lock = threading.RLock()
    service._cleanup_tasks = {}
    session = AvatarRuntimeSessionResponse(session_id="avatar_test", profile_id=uuid4(),
        provider="tavus", transport="livekit", created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=-1 if expired else 60))
    service._sessions = {session.session_id: StoredRuntimeSession(session=session)}
    service.tavus_adapter = SimpleNamespace(close_session=AsyncMock())
    return service, session


def test_close_route_resolves_profile_and_keeps_failed_cleanup_retryable(monkeypatch):
    service, session = runtime_service(expired=True)
    monkeypatch.setattr(AvatarRuntimeSessionService, "shared", lambda: service)
    authorization = Mock()
    monkeypatch.setattr(avatar_runtime, "require_profile_access", authorization)
    service.tavus_adapter.close_session.side_effect = TavusRuntimeConnectionError("private provider details")
    with pytest.raises(HTTPException) as error:
        asyncio.run(avatar_runtime.close_avatar_runtime_session(session.session_id, principal=object()))
    assert error.value.status_code == 503
    assert "private" not in str(error.value.detail)
    assert authorization.call_args.kwargs["profile_id"] == session.profile_id
    assert service.require_session_profile_id(session.session_id) == session.profile_id
    service.tavus_adapter.close_session.side_effect = None
    assert asyncio.run(avatar_runtime.close_avatar_runtime_session(session.session_id, principal=object())).status_code == 204
    with pytest.raises(AvatarRuntimeSessionNotFoundError):
        service.require_session_profile_id(session.session_id)


@pytest.mark.parametrize("operation", ["close", "interrupt"])
def test_foreign_profile_cannot_close_or_interrupt_session(monkeypatch, operation):
    service, session = runtime_service()
    monkeypatch.setattr(AvatarRuntimeSessionService, "shared", lambda: service)
    monkeypatch.setattr(avatar_runtime, "require_profile_access", Mock(side_effect=HTTPException(404, "Profile not found.")))
    service.interrupt_session = AsyncMock()
    action = avatar_runtime.close_avatar_runtime_session if operation == "close" else avatar_runtime.interrupt_avatar_runtime_session
    with pytest.raises(HTTPException):
        asyncio.run(action(session.session_id, principal=object()))
    service.tavus_adapter.close_session.assert_not_called()
    service.interrupt_session.assert_not_called()


def test_expiry_does_not_drop_failed_cleanup_or_spawn_duplicate_tasks():
    async def exercise():
        service, session = runtime_service(expired=True)
        service.tavus_adapter.close_session.side_effect = TavusRuntimeConnectionError("unavailable")
        service._remove_expired_sessions()
        service._remove_expired_sessions()
        assert len(service._cleanup_tasks) == 1
        task = service._cleanup_tasks[session.session_id]
        with pytest.raises(AvatarRuntimeProviderUnavailableError):
            await task
        await asyncio.sleep(0)
        assert session.session_id in service._sessions
        assert service._cleanup_tasks == {}
        service.tavus_adapter.close_session.side_effect = None
        service._remove_expired_sessions()
        await service._cleanup_tasks[session.session_id]
        assert session.session_id not in service._sessions
    asyncio.run(exercise())


def test_handle_is_retained_and_audio_stays_blocked_until_remote_cleanup_succeeds():
    async def exercise():
        adapter = AvatarRuntimeTavusAdapter.__new__(AvatarRuntimeTavusAdapter)
        adapter._handles_lock = asyncio.Lock()
        room = SimpleNamespace(isconnected=lambda: True)
        handle = TavusRuntimeHandle(session_id="avatar_test", room_name="test-room", avatar_identity="test-avatar",
            dispatch_id="test-dispatch", room=room, audio_output=object())
        adapter._handles = {handle.session_id: handle}
        adapter._prepare_output_for_disconnect = AsyncMock()
        adapter._disconnect_room = AsyncMock()
        adapter._delete_remote_resources = AsyncMock(side_effect=TavusRuntimeConnectionError("unavailable"))
        with pytest.raises(TavusRuntimeConnectionError):
            await adapter.close_session(handle.session_id)
        assert adapter._handles[handle.session_id] is handle
        assert handle.closing and not handle.closed
        with pytest.raises(TavusRuntimeConnectionError):
            await adapter._require_handle(handle.session_id)
        adapter._delete_remote_resources.side_effect = None
        await adapter.close_session(handle.session_id)
        assert handle.closed and handle.session_id not in adapter._handles
    asyncio.run(exercise())


@pytest.mark.parametrize("error_code", ["not_found", "permission_denied", "timeout"])
def test_remote_cleanup_attempts_room_even_if_dispatch_fails(error_code):
    adapter = AvatarRuntimeTavusAdapter.__new__(AvatarRuntimeTavusAdapter)
    adapter.token_service = SimpleNamespace(is_configured=True)
    error = TimeoutError() if error_code == "timeout" else api.TwirpError(error_code, "private details", status=404 if error_code == "not_found" else 403)
    client = SimpleNamespace(agent_dispatch=SimpleNamespace(delete_dispatch=AsyncMock(side_effect=error)),
        room=SimpleNamespace(delete_room=AsyncMock()), aclose=AsyncMock())
    adapter._make_api_client = lambda: client
    if error_code == "not_found":
        asyncio.run(adapter._delete_remote_resources(room_name="test-room", dispatch_id="test-dispatch"))
    else:
        with pytest.raises(TavusRuntimeConnectionError) as caught:
            asyncio.run(adapter._delete_remote_resources(room_name="test-room", dispatch_id="test-dispatch"))
        assert "private" not in str(caught.value)
    client.room.delete_room.assert_awaited_once()
    client.aclose.assert_awaited_once()


@pytest.mark.parametrize("scenario", ["ended", "active", "wrong_id", "still_active", "unauthorized", "end_failed", "timeout"])
def test_tavus_end_requires_verified_matching_terminal_status(monkeypatch, scenario):
    monkeypatch.setenv("TAVUS_API_KEY", "test-key")
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.headers["x-api-key"] == "test-key"
        if scenario == "timeout":
            raise httpx.ReadTimeout("private transport details", request=request)
        if scenario == "unauthorized":
            return httpx.Response(401)
        if request.method == "POST":
            return httpx.Response(503 if scenario == "end_failed" else 200)
        return httpx.Response(200, json={
            "conversation_id": "another-conversation" if scenario == "wrong_id" else "c_test",
            "status": "ended" if scenario == "ended" or (len(calls) == 3 and scenario != "still_active") else "active",
        })

    original_client = httpx.AsyncClient
    monkeypatch.setattr("app.services.avatar_provider_service.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    if scenario in {"ended", "active"}:
        asyncio.run(AvatarProviderService().end_tavus_conversation(conversation_id="c_test"))
        assert calls == (["GET"] if scenario == "ended" else ["GET", "POST", "GET"])
    else:
        with pytest.raises(RuntimeError) as error:
            asyncio.run(AvatarProviderService().end_tavus_conversation(conversation_id="c_test"))
        assert "private" not in str(error.value)
        if scenario in {"wrong_id", "unauthorized", "timeout"}:
            assert calls == ["GET"]
