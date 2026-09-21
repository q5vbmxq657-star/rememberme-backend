import asyncio
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.routes.tavus_webhook as module
from app.services.avatar_provider_service import AvatarProviderStatusUnavailableError
from app.services.avatar_provider_service import AvatarProviderService
import app.services.avatar_provider_service as provider_module
from test_tavus_face_training_contract import FakeHTTPClient, FakeRepository, valid_tavus_consent


@pytest.mark.parametrize('secret', [None, 'wrong', '\u00fc'])
def test_invalid_callback_auth_never_processes_event(monkeypatch, secret):
    monkeypatch.setenv('TAVUS_WEBHOOK_SECRET', 'expected')
    request = Mock()
    request.scope = {'query_string': b'secret=private-value'}
    request.json = AsyncMock()
    apply = Mock()
    monkeypatch.setattr(module.avatar_provider_service, 'apply_tavus_webhook', apply)
    with pytest.raises(HTTPException) as error:
        asyncio.run(module.receive_tavus_webhook(request, secret, None, None))
    assert error.value.status_code == 401
    request.json.assert_not_awaited()
    apply.assert_not_called()
    assert request.scope['query_string'] == b''


@pytest.mark.parametrize('payload', [[], 'invalid', None])
def test_malformed_callback_never_processes_event(monkeypatch, payload):
    monkeypatch.setenv('TAVUS_WEBHOOK_SECRET', 'expected')
    request = Mock()
    request.scope = {'query_string': b'secret=private-value'}
    request.json = AsyncMock(return_value=payload)
    apply = Mock()
    monkeypatch.setattr(module.avatar_provider_service, 'apply_tavus_webhook', apply)
    with pytest.raises(HTTPException) as error:
        asyncio.run(module.receive_tavus_webhook(request, 'expected', None, None))
    assert error.value.status_code == 422
    apply.assert_not_called()


def test_unsaved_callback_is_retryable_not_acknowledged(monkeypatch):
    monkeypatch.setenv('TAVUS_WEBHOOK_SECRET', 'expected')
    request = Mock()
    request.scope = {'query_string': b'secret=private-value'}
    request.json = AsyncMock(return_value={'face_id': 'face', 'status': 'completed'})
    monkeypatch.setattr(module.avatar_provider_service, 'apply_tavus_webhook',
        Mock(side_effect=AvatarProviderStatusUnavailableError('private DB detail')))
    with pytest.raises(HTTPException) as error:
        asyncio.run(module.receive_tavus_webhook(request, 'expected', None, None))
    assert error.value.status_code == 503
    assert error.value.headers['Retry-After'] == '12'
    assert 'private' not in error.value.detail


@pytest.mark.parametrize('configured_url', [
    'https://stay.example/callback',
    'https://stay.example/callback?secret=outdated&source=tavus#fragment',
])
def test_callback_uses_encoded_current_secret(monkeypatch, valid_tavus_consent, configured_url):
    monkeypatch.setenv('TAVUS_API_KEY', 'contract-key')
    monkeypatch.setenv('TAVUS_CALLBACK_URL', configured_url)
    monkeypatch.setenv('TAVUS_WEBHOOK_SECRET', 'test+secret&with=special?characters')
    monkeypatch.setattr(provider_module.httpx, 'AsyncClient', FakeHTTPClient)
    service = AvatarProviderService()
    service._profile_repository = FakeRepository()
    monkeypatch.setattr(service, '_extract_tavus_training_source',
        lambda *args, **kwargs: ('train_video_url', 'https://stay.example/owned-lease'))
    asyncio.run(service.submit(provider='tavus', profile_id=str(uuid4()),
        package_record_id=str(uuid4()), package={}))
    callback = urlsplit(FakeHTTPClient.last_payload['callback_url'])
    assert parse_qs(callback.query)['secret'] == ['test+secret&with=special?characters']
    assert callback.fragment == ''


def test_bad_callback_configuration_never_creates_stranded_job(monkeypatch, valid_tavus_consent):
    monkeypatch.setenv('TAVUS_API_KEY', 'contract-key')
    monkeypatch.setenv('TAVUS_CALLBACK_URL', 'http://insecure.example/callback')
    monkeypatch.setenv('TAVUS_WEBHOOK_SECRET', 'test-secret')
    service = AvatarProviderService()
    repository = Mock()
    repository.get_avatar_training_request.return_value = None
    service._profile_repository = repository
    monkeypatch.setattr(service, '_extract_tavus_training_source',
        lambda *args, **kwargs: ('train_video_url', 'https://stay.example/owned-lease'))
    with pytest.raises(AvatarProviderStatusUnavailableError):
        asyncio.run(service.submit(provider='tavus', profile_id=str(uuid4()),
            package_record_id=str(uuid4()), package={}))
    repository.create_training_job.assert_not_called()
