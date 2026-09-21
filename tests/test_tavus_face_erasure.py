import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.avatar_provider_service as provider_module


@pytest.mark.parametrize("status", [200, 404, 202, 401, 429, 500])
def test_face_erasure_requires_hard_delete_and_definite_response(monkeypatch, status):
    monkeypatch.setenv("TAVUS_API_KEY", "test-only")
    client = AsyncMock()
    client.delete.return_value = Mock(status_code=status)
    client.__aenter__.return_value = client
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", Mock(return_value=client))
    operation = provider_module.AvatarProviderService().delete_tavus_face(face_id="r_owned")
    if status in {200, 404}:
        asyncio.run(operation)
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(operation)
    client.delete.assert_awaited_once_with(
        "https://tavusapi.com/v2/faces/r_owned",
        params={"hard": "true"}, headers={"x-api-key": "test-only"},
    )


@pytest.mark.parametrize("identifier", ["", "../other", "face?hard=false", "face/path"])
def test_invalid_face_identity_never_reaches_provider(monkeypatch, identifier):
    factory = Mock()
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", factory)
    with pytest.raises(ValueError):
        asyncio.run(provider_module.AvatarProviderService().delete_tavus_face(face_id=identifier))
    factory.assert_not_called()
