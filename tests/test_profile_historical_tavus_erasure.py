import asyncio
from unittest.mock import Mock, AsyncMock
from uuid import uuid4

import pytest

from app.services.profile_erasure_service import ProfileErasureService, ProfileErasureServiceError


def service_for(payload, **fields):
    repository, provider = Mock(), AsyncMock()
    job_id = uuid4()
    repository.list_training_jobs.return_value = [dict(job_id=job_id, training_type='avatar',
        provider='tavus', status='failed', provider_payload=payload, **fields)]
    service = ProfileErasureService(repository=repository, avatar_provider=provider,
        voice_service=AsyncMock(), media_storage=Mock(), runtime_cleanup_repository=Mock(),
        openai_realtime_registry=Mock())
    return service, repository, provider, job_id


def test_late_face_result_is_deleted_with_face_api():
    service, repository, provider, job = service_for({'face_id': 'late-face'}, provider_job_id='tavus:late-face')
    asyncio.run(service._delete_historical_tavus(uuid4()))
    provider.delete_tavus_face.assert_awaited_once_with(face_id='late-face')
    provider.delete_tavus_identity.assert_not_called()
    repository.update_training_job.assert_called_once_with(job, status='deleted')


def test_historical_replica_and_persona_are_both_deleted():
    service, repository, provider, job = service_for({'replica_id': 'old-replica', 'persona_id': 'old-persona'})
    asyncio.run(service._delete_historical_tavus(uuid4()))
    provider.delete_tavus_identity.assert_awaited_once_with(replica_id='old-replica', persona_id='old-persona')
    repository.update_training_job.assert_called_once_with(job, status='deleted')


def test_failed_remote_cleanup_preserves_job():
    service, repository, provider, _ = service_for({'face_id': 'late-face'})
    provider.delete_tavus_face.side_effect = RuntimeError('unavailable')
    with pytest.raises(RuntimeError):
        asyncio.run(service._delete_historical_tavus(uuid4()))
    repository.update_training_job.assert_not_called()


def test_ambiguous_resource_type_blocks_deletion():
    service, repository, provider, _ = service_for({}, provider_job_id='tavus:unknown-type')
    with pytest.raises(ProfileErasureServiceError):
        asyncio.run(service._delete_historical_tavus(uuid4()))
    repository.update_training_job.assert_not_called()
