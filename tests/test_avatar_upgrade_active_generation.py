from uuid import uuid4

import psycopg
import pytest

from test_avatar_activation_privacy import avatar_job, activate


@pytest.mark.parametrize('status', ['training', 'failed', 'ready'])
def test_upgrade_only_replaces_verified_avatar_when_ready(avatar_job, status):
    url, repository, profile, _ = avatar_job
    activate(repository, profile)
    with psycopg.connect(url) as connection:
        connection.execute('UPDATE digital_human_profiles SET consent_verified=TRUE, runtime_verified_at=NOW() WHERE profile_id=%s', (profile,))
        connection.execute('UPDATE profile_purpose_consents SET purposes=%s WHERE profile_id=%s',
            (['photo_likeness', 'video_motion', 'voice_synthesis', 'provider_processing'], profile))
    active = repository.require(profile)
    candidate_id = uuid4()
    repository.create_training_job(job_id=candidate_id, profile_id=profile, training_type='avatar',
        provider='tavus', status='training', training_version=2, idempotency_key=str(candidate_id),
        request_payload={'train_video_url': 'https://stay.example/video', '_stay_consent_revision': 1})
    repository.update_training_job(candidate_id, status='training', provider_job_id='tavus:upgrade-face')
    outcome = repository.apply_avatar_training_result(profile_id=profile, job_id=candidate_id,
        provider='tavus', status=status, provider_job_id='tavus:upgrade-face',
        replica_id='upgrade-face', provider_payload={})
    result = repository.require(profile)
    assert outcome['profile_updated'] is (status == 'ready')
    if status == 'ready':
        assert result.avatar_replica_id == 'upgrade-face'
        assert result.avatar_training_job_id == 'tavus:upgrade-face'
        assert result.runtime_verified_at is None
    else:
        assert result == active
        assert repository.require(profile) == active
        assert result.has_runtime_avatar
        assert repository.get_training_job(candidate_id)['status'] == status
