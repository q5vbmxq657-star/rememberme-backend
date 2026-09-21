import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.avatar_provider_service as module
from app.services.avatar_provider_service import AvatarProviderService, AvatarProviderJobState, AvatarProviderStatusUnavailableError


@pytest.mark.parametrize('status', ['', 'new_provider_state', None, 100])
def test_unknown_callback_status_never_becomes_training(status):
    with pytest.raises(AvatarProviderStatusUnavailableError):
        AvatarProviderService()._normalize_tavus_status_from_payload({'status': status})


def test_unknown_poll_status_preserves_last_confirmed_state(monkeypatch):
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only')
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = Mock(status_code=200, json=Mock(return_value={'status': 'new_provider_state'}))
    monkeypatch.setattr(module.httpx, 'AsyncClient', Mock(return_value=client))
    service = AvatarProviderService()
    service._sync_tavus_status_to_profile = Mock()
    state = AvatarProviderJobState('tavus:face', 'face', 'ready', None)
    with pytest.raises(AvatarProviderStatusUnavailableError):
        asyncio.run(service._fetch_tavus_status(state))
    assert state.status == 'ready'
    service._sync_tavus_status_to_profile.assert_not_called()


@pytest.mark.parametrize('payload,code', [
    ({'status': 'completed'}, 200),
    ({'face_id': 'other_profile_face', 'status': 'completed'}, 200),
    ({'replica_id': 'other_profile_face', 'status': 'completed'}, 200),
    ({'face_id': 'face', 'status': 'completed'}, 302),
])
def test_unverified_provider_response_cannot_activate_avatar(monkeypatch, payload, code):
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only')
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = Mock(status_code=code, json=Mock(return_value=payload))
    monkeypatch.setattr(module.httpx, 'AsyncClient', Mock(return_value=client))
    service = AvatarProviderService()
    service._sync_tavus_status_to_profile = Mock()
    state = AvatarProviderJobState('tavus:face', 'face', 'training', None)
    with pytest.raises(AvatarProviderStatusUnavailableError):
        asyncio.run(service._fetch_tavus_status(state))
    assert state.status == 'training'
    service._sync_tavus_status_to_profile.assert_not_called()


@pytest.mark.parametrize('code,message,expected', [
    (402, 'private account balance', 'usage limit'),
    (401, 'private api key', 'not configured'),
    (403, 'private provider permission', 'not configured'),
    (400, 'invalid voice_name private configuration', 'not configured'),
    (413, 'private file path', 'smaller file'),
    (422, 'unable to download url https://private.example/token', 'could not download'),
    (422, 'multiple faces in private file', 'reported more than one face'),
    (422, 'no face in private file', 'could not identify a face'),
    (422, 'private diagnostic', 'Your media is saved'),
])
def test_failure_copy_distinguishes_cause_without_exposing_provider_details(code, message, expected):
    text = AvatarProviderService()._normalize_tavus_error(code, message)
    assert expected in text
    assert 'private' not in text
    assert 'https://' not in text
