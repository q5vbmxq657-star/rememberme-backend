import asyncio
import httpx
import pytest
from app.services.elevenlabs_voice_service import ElevenLabsVoiceService, ElevenLabsVoiceProviderError


@pytest.mark.parametrize("status,code,success", [
    (400, "voice_not_found", True),
    (400, "invalid_request", False),
    (401, "voice_not_found", False),
    (403, "missing_permissions", False),
    (429, "rate_limit", False),
    (204, None, True),
])
def test_voice_deletion_only_accepts_confirmed_absence(monkeypatch, status, code, success):
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def delete(self, url, **kwargs):
            return httpx.Response(status, json={"detail": {"status": code}})
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    service = object.__new__(ElevenLabsVoiceService)
    service.api_key = "test-only"
    if success:
        asyncio.run(service.delete_voice_resource(voice_id="test-voice"))
    else:
        with pytest.raises(ElevenLabsVoiceProviderError):
            asyncio.run(service.delete_voice_resource(voice_id="test-voice"))
