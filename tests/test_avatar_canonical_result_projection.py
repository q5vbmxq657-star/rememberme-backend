import asyncio
from unittest.mock import AsyncMock, Mock

import psycopg
import pytest

import app.services.avatar_provider_service as module
from app.services.avatar_provider_service import (
    AvatarProviderService, AvatarProviderJobState, AvatarProviderStatusUnavailableError,
)
from test_avatar_activation_privacy import avatar_job


def service_for(fixture):
    _, repository, _, _ = fixture
    service = AvatarProviderService()
    service._profile_repository = repository
    return service


def test_callback_returns_canonical_ready_after_delayed_started(avatar_job):
    service = service_for(avatar_job)
    assert service.apply_tavus_webhook({'face_id': 'test-face', 'status': 'completed'}).status == 'ready'
    delayed = service.apply_tavus_webhook({'face_id': 'test-face', 'status': 'started'})
    assert delayed.status == 'ready'
    assert delayed.error_message is None


def test_poll_returns_canonical_ready_after_out_of_order_provider_read(avatar_job, monkeypatch):
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only')
    service = service_for(avatar_job)
    service.apply_tavus_webhook({'face_id': 'test-face', 'status': 'completed'})
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = Mock(status_code=200, json=Mock(return_value={
        'face_id': 'test-face', 'status': 'started'}))
    monkeypatch.setattr(module.httpx, 'AsyncClient', Mock(return_value=client))
    state = asyncio.run(service.status('tavus:test-face'))
    assert state.status == 'ready'
    assert avatar_job[1].require(avatar_job[2]).avatar_training_status == 'ready'


@pytest.mark.parametrize('terminal', ['cancelled', 'deleted'])
def test_cancelled_result_never_returns_ready_or_an_avatar(avatar_job, terminal):
    service = service_for(avatar_job)
    avatar_job[1].update_training_job(avatar_job[3], status=terminal)
    state = service.apply_tavus_webhook({'face_id': 'test-face', 'status': 'completed'})
    assert state.status == 'failed'
    assert state.external_avatar_id is None
    assert 'no longer active' in state.error_message


def test_database_failure_never_acknowledges_unsaved_success():
    service = AvatarProviderService()
    repository = Mock()
    repository.get_training_job_by_provider_job_id.side_effect = psycopg.OperationalError('private DB details')
    service._profile_repository = repository
    with pytest.raises(AvatarProviderStatusUnavailableError, match='could not be saved'):
        service._sync_tavus_status_to_profile(
            state=AvatarProviderJobState('tavus:test-face', 'test-face', 'ready', None),
            provider_payload={'face_id': 'test-face', 'status': 'completed'})
    repository.apply_avatar_training_result.assert_not_called()
