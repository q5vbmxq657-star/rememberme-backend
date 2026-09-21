import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from unittest.mock import patch
import threading
from datetime import datetime, timezone
import psycopg
from uuid import uuid4

import pytest

from app.services.avatar_runtime_session_service import AvatarRuntimeSessionService, AvatarRuntimeConflictError, AvatarRuntimeProviderUnavailableError
from app.schemas.avatar_runtime import AvatarRuntimeSessionCreateRequest
from app.services.digital_human_profile_repository import StaleAvatarTrainingError


def setup_service():
    profile_id = uuid4()
    profile = SimpleNamespace(has_runtime_avatar=True, avatar_provider='tavus',
        avatar_replica_id='face-one', avatar_persona_id=None, avatar_training_job_id='tavus:face-one',
        training_version=1, avatar_training_status='ready', consent_verified=True)
    service = AvatarRuntimeSessionService.__new__(AvatarRuntimeSessionService)
    service.profile_repository = Mock()
    service.profile_repository.require.return_value = profile
    service.provider_registry = Mock()
    service.provider_registry.require_provider.return_value = ('tavus', {})
    service.tavus_adapter = AsyncMock()
    service._lock = threading.RLock()
    service._sessions = {}
    return service, profile, AvatarRuntimeSessionCreateRequest(profile_id=profile_id, display_name='Test', maximum_accepted_latency_ms=2000)


@pytest.mark.parametrize('field,value', [('avatar_replica_id', 'face-two'),
    ('avatar_training_job_id', 'new-job'), ('training_version', 2),
    ('avatar_training_status', 'training'), ('consent_verified', False)])
def test_material_replacement_during_activation_closes_remote_session(field, value):
    service, profile, request = setup_service()
    async def start(**kwargs):
        setattr(profile, field, value)
        return SimpleNamespace(provider_avatar_id='face-one', metadata={})
    service.tavus_adapter.start_session.side_effect = start
    with pytest.raises(AvatarRuntimeConflictError):
        asyncio.run(service.create_session(request))
    service.tavus_adapter.close_session.assert_awaited_once()
    service.profile_repository.mark_runtime_verified.assert_not_called()


def test_database_failure_survives_secondary_cleanup_failure():
    service, profile, request = setup_service()
    original = psycopg.OperationalError('database disconnected')
    service.profile_repository.require.side_effect = [profile, original]
    service.tavus_adapter.start_session.return_value = SimpleNamespace()
    service.tavus_adapter.close_session.side_effect = RuntimeError('cleanup unavailable')
    with pytest.raises(AvatarRuntimeProviderUnavailableError) as error:
        asyncio.run(service.create_session(request))
    assert error.value.__cause__ is original
    service.tavus_adapter.close_session.assert_awaited_once()


@pytest.mark.parametrize('failure_stage', ['projection', 'persistence'])
@pytest.mark.parametrize('error_type,mapped', [(psycopg.OperationalError, AvatarRuntimeProviderUnavailableError),
    (StaleAvatarTrainingError, AvatarRuntimeConflictError), (asyncio.CancelledError, asyncio.CancelledError)])
def test_post_activation_failure_closes_session_and_preserves_original(failure_stage, error_type, mapped):
    service, profile, request = setup_service()
    original = error_type('persistence unavailable')
    remote = SimpleNamespace(provider_avatar_id='face-one',
        descriptor=SimpleNamespace(expires_at=datetime.now(timezone.utc)),
        metadata={key: 'true' for key in ('remote_session_verified', 'avatar_participant_verified', 'avatar_video_track_verified')})
    service.tavus_adapter.start_session.return_value = remote
    service.tavus_adapter.close_session.side_effect = RuntimeError('cleanup unavailable')
    service.profile_repository.mark_runtime_verified.side_effect = original
    with patch('app.services.avatar_runtime_session_service.AvatarRuntimeSessionResponse') as response:
        if failure_stage == 'projection':
            response.side_effect = original
        else:
            response.side_effect = lambda **kwargs: SimpleNamespace(**kwargs)
        with pytest.raises(mapped) as error:
            asyncio.run(service.create_session(request))
    assert error.value is original if mapped is asyncio.CancelledError else error.value.__cause__ is original
    assert service._sessions == {}
    service.tavus_adapter.close_session.assert_awaited_once()
    if failure_stage == 'persistence':
        service.profile_repository.mark_runtime_verified.assert_called_once_with(request.profile_id, expected_profile=profile)


@pytest.mark.parametrize('error_type,mapped', [(psycopg.OperationalError, AvatarRuntimeProviderUnavailableError),
    (StaleAvatarTrainingError, AvatarRuntimeConflictError), (asyncio.CancelledError, asyncio.CancelledError)])
def test_initial_profile_lookup_error_mapping(error_type, mapped):
    service, _, request = setup_service()
    original = error_type('private detail')
    service.profile_repository.require.side_effect = original
    with pytest.raises(mapped) as error:
        asyncio.run(service.create_session(request))
    assert error.value is original if mapped is asyncio.CancelledError else error.value.__cause__ is original
    service.tavus_adapter.start_session.assert_not_called()


@pytest.mark.parametrize('remote_id,metadata', [
    ('face-one', {'remote_session_verified': 'true'}),
    ('other-profile-face', {'remote_session_verified': 'true', 'avatar_participant_verified': 'true',
                            'avatar_video_track_verified': 'true'}),
])
def test_static_or_unverified_adapter_output_cannot_succeed(remote_id, metadata):
    service, profile, request = setup_service()
    service.tavus_adapter.start_session.return_value = SimpleNamespace(
        provider_avatar_id=remote_id, metadata=metadata)
    with pytest.raises(AvatarRuntimeProviderUnavailableError, match='video could not be verified'):
        asyncio.run(service.create_session(request))
    service.tavus_adapter.close_session.assert_awaited_once()
    service.profile_repository.mark_runtime_verified.assert_not_called()
