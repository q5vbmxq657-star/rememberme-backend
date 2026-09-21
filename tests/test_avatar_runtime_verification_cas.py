from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.services.digital_human_profile_repository import StaleAvatarTrainingError, DigitalHumanProfileRepositoryError
from test_avatar_activation_privacy import avatar_job, activate


def ready_profile(fixture):
    url, repository, profile, _ = fixture
    activate(repository, profile)
    with psycopg.connect(url) as connection:
        connection.execute('UPDATE digital_human_profiles SET consent_verified=TRUE WHERE profile_id=%s', (profile,))
    return repository.require(profile)


def test_verification_applies_to_exact_ready_snapshot(avatar_job):
    expected = ready_profile(avatar_job)
    result = avatar_job[1].mark_runtime_verified(avatar_job[2], expected_profile=expected)
    assert result.runtime_verified_at is not None


@pytest.mark.parametrize('field,value', [
    ('avatar_provider', 'other'), ('avatar_replica_id', 'new-face'),
    ('avatar_persona_id', 'new-persona'), ('avatar_training_job_id', 'new-job'),
    ('training_version', 2), ('avatar_training_status', 'training'), ('consent_verified', False),
])
def test_changed_material_cannot_receive_old_runtime_verification(avatar_job, field, value):
    url, repository, profile, _ = avatar_job
    expected = ready_profile(avatar_job)
    with psycopg.connect(url) as connection:
        connection.execute(sql.SQL('UPDATE digital_human_profiles SET {}=%s WHERE profile_id=%s').format(sql.Identifier(field)),
            (value, profile))
    with pytest.raises(StaleAvatarTrainingError):
        repository.mark_runtime_verified(profile, expected_profile=expected)
    assert repository.require(profile).runtime_verified_at is None


def test_erasure_prevents_runtime_verification(avatar_job):
    url, repository, profile, _ = avatar_job
    expected = ready_profile(avatar_job)
    with psycopg.connect(url) as connection:
        connection.execute('INSERT INTO digital_human_profile_erasure_requests (request_id,profile_id,idempotency_key) VALUES (%s,%s,%s)',
            (uuid4(), profile, str(uuid4())))
    with pytest.raises(DigitalHumanProfileRepositoryError):
        repository.mark_runtime_verified(profile, expected_profile=expected)
    assert repository.require(profile).runtime_verified_at is None


def test_foreign_snapshot_cannot_verify_profile(avatar_job):
    expected = replace(ready_profile(avatar_job), profile_id=uuid4())
    with pytest.raises(StaleAvatarTrainingError):
        avatar_job[1].mark_runtime_verified(avatar_job[2], expected_profile=expected)


def test_concurrent_material_change_is_observed_after_lock(avatar_job):
    url, repository, profile, _ = avatar_job
    expected = ready_profile(avatar_job)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with psycopg.connect(url) as connection:
            connection.execute('SELECT profile_id FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE', (profile,))
            future = executor.submit(repository.mark_runtime_verified, profile, expected_profile=expected)
            connection.execute('UPDATE digital_human_profiles SET training_version=2 WHERE profile_id=%s', (profile,))
            connection.commit()
        with pytest.raises(StaleAvatarTrainingError):
            future.result(timeout=10)
    assert repository.require(profile).runtime_verified_at is None


def test_new_training_clears_previous_avatar_readiness_evidence(avatar_job):
    _, repository, profile, _ = avatar_job
    expected = ready_profile(avatar_job)
    repository.mark_runtime_verified(profile, expected_profile=expected)
    new_job = uuid4()
    repository.create_training_job(job_id=new_job, profile_id=profile, training_type='avatar',
        provider='tavus', status='created', training_version=2, idempotency_key=str(uuid4()),
        request_payload={'train_image_url': 'https://stay.example/new-photo', '_stay_consent_revision': 1})
    repository.update_training_job(new_job, status='training', provider_job_id='tavus:new-face')
    current = repository.set_avatar_training(profile, provider='tavus', status='training',
        provider_job_id='tavus:new-face', replica_id='new-face', training_job_id=new_job)
    assert current.avatar_ready_at is None
    assert current.runtime_verified_at is None
