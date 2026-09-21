from uuid import uuid4

import pytest

from app.services.digital_human_profile_repository import DigitalHumanProfileRepositoryError
from test_avatar_activation_privacy import avatar_job


def recovery_job(avatar_job):
    _, repository, profile, _ = avatar_job
    job_id = uuid4()
    correlation = f'stay_face_{job_id.hex}'
    repository.create_training_job(job_id=job_id, profile_id=profile, training_type='avatar', provider='tavus',
        status='submitted', training_version=2, idempotency_key=str(job_id),
        request_payload={'_stay_face_name': correlation, '_stay_consent_revision': 1,
                         'train_image_url': 'https://stay.example/photo'})
    return repository, job_id, correlation


def test_recovery_records_once_without_overwriting_later_outcome(avatar_job):
    repository, job_id, correlation = recovery_job(avatar_job)
    payload = {'face_id': 'recovered', 'face_name': correlation, 'status': 'started'}
    result = repository.adopt_reconciled_avatar(job_id=job_id, correlation_name=correlation,
        face_id='recovered', provider_payload=payload)
    assert result['status'] == 'training'
    assert result['provider_job_id'] == 'tavus:recovered'
    repository.update_training_job(job_id, status='deleted')
    result = repository.adopt_reconciled_avatar(job_id=job_id, correlation_name=correlation,
        face_id='recovered', provider_payload=payload)
    assert result['status'] == 'deleted'


@pytest.mark.parametrize('mutation', ['correlation', 'payload_name', 'payload_id'])
def test_recovery_rejects_mismatched_creation_identity(avatar_job, mutation):
    repository, job_id, correlation = recovery_job(avatar_job)
    payload = {'face_id': 'recovered', 'face_name': correlation}
    if mutation == 'correlation':
        correlation = f'stay_face_{uuid4().hex}'
        payload['face_name'] = correlation
    elif mutation == 'payload_name':
        payload['face_name'] = 'other'
    else:
        payload['face_id'] = 'other'
    with pytest.raises(DigitalHumanProfileRepositoryError):
        repository.adopt_reconciled_avatar(job_id=job_id, correlation_name=correlation,
            face_id='recovered', provider_payload=payload)
    assert repository.get_training_job(job_id)['provider_job_id'] is None
