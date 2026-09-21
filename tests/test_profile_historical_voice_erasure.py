import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.services.profile_erasure_service import ProfileErasureService, ProfileErasureServiceError


def make_service(jobs):
    repository = Mock()
    repository.list_training_jobs.return_value = jobs
    voice = AsyncMock()
    return ProfileErasureService(repository=repository, voice_service=voice,
                                 avatar_provider=AsyncMock(), media_storage=Mock(),
                                 runtime_cleanup_repository=Mock(), openai_realtime_registry=Mock()), repository, voice


def test_stale_voice_resources_are_deleted_and_acknowledged():
    job_id = uuid4()
    service, repository, voice = make_service([{
        'job_id': job_id, 'training_type': 'voice', 'provider': 'elevenlabs',
        'provider_job_id': 'stale-voice', 'status': 'failed',
    }])
    asyncio.run(service._delete_historical_voices(uuid4()))
    voice.delete_voice_resource.assert_awaited_once_with(voice_id='stale-voice')
    repository.update_training_job.assert_called_once_with(job_id, status='deleted')


def test_provider_failure_preserves_cleanup_evidence():
    service, repository, voice = make_service([{
        'job_id': uuid4(), 'training_type': 'voice', 'provider': 'elevenlabs',
        'provider_job_id': 'stale-voice', 'status': 'ready',
    }])
    voice.delete_voice_resource.side_effect = RuntimeError('unavailable')
    with pytest.raises(RuntimeError):
        asyncio.run(service._delete_historical_voices(uuid4()))
    repository.update_training_job.assert_not_called()


@pytest.mark.parametrize('status', ['created', 'submitted', 'training'])
def test_unresolved_provider_creation_blocks_graph_deletion(status):
    service, repository, voice = make_service([{
        'training_type': 'voice', 'provider': 'elevenlabs',
        'provider_job_id': None, 'status': status,
    }])
    with pytest.raises(ProfileErasureServiceError, match='reconciliation'):
        asyncio.run(service._delete_historical_voices(uuid4()))
    repository.update_training_job.assert_not_called()
