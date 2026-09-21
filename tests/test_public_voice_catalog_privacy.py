import asyncio
from unittest.mock import AsyncMock, Mock

import httpx

from app.services.elevenlabs_voice_service import ElevenLabsVoiceService


def test_general_voice_catalog_never_exposes_profile_clones(monkeypatch):
    response = httpx.Response(200, json={"voices": [
        {"voice_id": "public", "name": "Public voice", "category": "premade", "samples": ["not-for-client"]},
        {"voice_id": "private", "name": "Another profile", "category": "cloned", "samples": ["private-audio"]},
        {"voice_id": "unknown", "name": "Unclassified"},
    ], "account_id": "private-account", "total_count": 999})
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response
    monkeypatch.setattr(httpx, "AsyncClient", Mock(return_value=client))
    service = ElevenLabsVoiceService.__new__(ElevenLabsVoiceService)
    service.api_key = "test-only"
    result = asyncio.run(service.list_voices())
    assert result == {"voices": [{"voice_id": "public", "name": "Public voice", "category": "premade"}]}
    assert client.get.call_args.kwargs["params"] == {"category": "premade"}
