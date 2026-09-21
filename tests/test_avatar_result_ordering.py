from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import json

import psycopg
import pytest

from app.services.digital_human_profile_repository import StaleAvatarTrainingError
from app.services.digital_human_profile_repository import DigitalHumanProfileRepositoryError
from test_avatar_activation_privacy import avatar_job


def apply(fixture, status, **overrides):
    _, repo, profile, job = fixture
    parameters = dict(profile_id=profile, job_id=job, provider='tavus',
        provider_job_id='tavus:test-face', replica_id='test-face', status=status,
        provider_payload={'face_id':'test-face', 'status':status})
    parameters.update(overrides)
    return repo.apply_avatar_training_result(**parameters)


def test_ready_is_not_downgraded_by_late_started_callback(avatar_job):
    _, repo, profile, job = avatar_job
    first = apply(avatar_job, 'ready')
    before = repo.require(profile)
    result = apply(avatar_job, 'training')
    assert first['profile_updated'] and result['status'] == 'ready'
    assert not result['profile_updated']
    assert repo.require(profile) == before
    assert result['provider_payload']['status'] == 'ready'
    assert result['completed_at'] == first['completed_at']


@pytest.mark.parametrize('terminal', ['failed', 'cancelled', 'deleted'])
def test_terminal_job_cannot_be_activated_by_late_ready(avatar_job, terminal):
    _, repo, profile, job = avatar_job
    repo.update_training_job(job, status=terminal)
    before = repo.require(profile)
    result = apply(avatar_job, 'ready')
    assert result['status'] == terminal and not result['profile_updated']
    assert repo.require(profile) == before


def test_legacy_split_methods_also_reject_ready_regression(avatar_job):
    _, repo, profile, job = avatar_job
    apply(avatar_job, 'ready')
    result = repo.update_training_job(job, status='training', provider_payload={'status':'training'})
    assert result['status'] == 'ready'
    with pytest.raises(StaleAvatarTrainingError):
        repo.set_avatar_training(profile, provider='tavus', status='training',
            provider_job_id='tavus:test-face', replica_id='test-face', training_job_id=job)
    assert repo.require(profile).avatar_training_status == 'ready'


def test_repeated_ready_preserves_timestamps(avatar_job):
    _, repo, profile, _ = avatar_job
    first = apply(avatar_job, 'ready')
    ready_at = repo.require(profile).avatar_ready_at
    second = apply(avatar_job, 'ready')
    assert second['completed_at'] == first['completed_at']
    assert repo.require(profile).avatar_ready_at == ready_at


def test_failed_projection_rolls_back_job_update(avatar_job, monkeypatch):
    _, repo, profile, job = avatar_job
    before = repo.get_training_job(job)
    def crash(*args, **kwargs):
        raise RuntimeError('Simulated database failure')
    monkeypatch.setattr(repo, 'set_avatar_training', crash)
    with pytest.raises(RuntimeError):
        apply(avatar_job, 'ready')
    assert repo.get_training_job(job) == before
    assert repo.require(profile).avatar_training_status == 'training'


def test_wrong_face_identity_cannot_modify_job_or_profile(avatar_job):
    _, repo, profile, job = avatar_job
    before = repo.get_training_job(job)
    with pytest.raises(StaleAvatarTrainingError):
        apply(avatar_job, 'ready', replica_id='foreign')
    assert repo.get_training_job(job) == before
    assert repo.require(profile).avatar_training_status == 'training'


@pytest.mark.parametrize('override', [dict(replica_id=None), dict(provider_payload={'face_id':'foreign'}), dict(job_id=uuid4())])
def test_missing_or_foreign_identity_cannot_activate(avatar_job, override):
    _, repo, profile, job = avatar_job
    before = repo.get_training_job(job)
    with pytest.raises(StaleAvatarTrainingError):
        apply(avatar_job, 'ready', **override)
    assert repo.get_training_job(job) == before
    assert repo.require(profile).avatar_training_status == 'training'


def test_erasure_blocks_atomic_result_and_job_change(avatar_job):
    url, repo, profile, job = avatar_job
    with psycopg.connect(url) as connection:
        connection.execute('INSERT INTO digital_human_profile_erasure_requests (request_id,profile_id,idempotency_key) VALUES (%s,%s,%s)',
            (uuid4(), profile, str(uuid4())))
    before = repo.get_training_job(job)
    with pytest.raises(DigitalHumanProfileRepositoryError):
        apply(avatar_job, 'ready')
    assert repo.get_training_job(job) == before


def test_newer_job_prevents_old_result_projection(avatar_job):
    _, repo, profile, _ = avatar_job
    repo.create_training_job(job_id=uuid4(), profile_id=profile, training_type='avatar',
        provider='tavus', status='created', training_version=2, idempotency_key=str(uuid4()), request_payload={})
    before = repo.require(profile)
    result = apply(avatar_job, 'ready')
    assert result['status'] == 'ready' and not result['profile_updated']
    assert repo.require(profile) == before


def test_revocation_keeps_provider_evidence_without_profile_activation(avatar_job):
    url, repo, profile, _ = avatar_job
    with psycopg.connect(url) as connection:
        connection.execute('UPDATE profile_purpose_consents SET revision=2 WHERE profile_id=%s', (profile,))
    result = apply(avatar_job, 'ready')
    assert result['status'] == 'ready' and not result['profile_updated']
    assert repo.require(profile).avatar_training_status == 'training'


@pytest.mark.parametrize('cancellation', ['cancelled', 'deleted'])
def test_concurrent_cancellation_wins_against_provider_result(avatar_job, cancellation):
    _, repo, _, job = avatar_job
    barrier = Barrier(2)
    def cancel():
        barrier.wait(timeout=5)
        return repo.update_training_job(job, status=cancellation)
    def ready():
        barrier.wait(timeout=5)
        return apply(avatar_job, 'ready')
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(cancel), executor.submit(ready)]
        for future in futures:
            future.result(timeout=10)
    assert repo.get_training_job(job)['status'] == cancellation
    assert apply(avatar_job, 'ready')['status'] == cancellation


def test_concurrent_started_and_ready_always_leave_ready(avatar_job):
    _, repo, profile, job = avatar_job
    barrier = Barrier(2)
    def callback(status):
        barrier.wait(timeout=5)
        return apply(avatar_job, status)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(callback, status) for status in ('training', 'ready')]
        for future in futures:
            future.result(timeout=10)
    assert repo.get_training_job(job)['status'] == 'ready'
    assert repo.require(profile).avatar_training_status == 'ready'


@pytest.mark.parametrize('terminal', ['cancelled', 'deleted', 'failed', 'ready'])
def test_late_create_identity_is_retained_without_reviving_terminal_job(avatar_job, terminal):
    url, repo, profile, job = avatar_job
    with psycopg.connect(url) as connection:
        connection.execute('UPDATE digital_human_training_jobs SET provider_job_id=NULL WHERE job_id=%s', (job,))
    terminal_job = repo.update_training_job(job, status=terminal, error_code='preserved')
    before = repo.require(profile)
    result = repo.update_training_job(job, status='training', provider_job_id='tavus:late-face',
        provider_payload={'face_id':'late-face', 'status':'started'})
    assert result['status'] == terminal
    assert result['provider_job_id'] == 'tavus:late-face'
    assert result['provider_payload']['face_id'] == 'late-face'
    assert result['error_code'] == 'preserved'
    assert result['completed_at'] == terminal_job['completed_at']
    assert repo.require(profile) == before
    with pytest.raises(StaleAvatarTrainingError):
        repo.update_training_job(job, status='training', provider_job_id='tavus:foreign',
            provider_payload={'face_id':'foreign'})
    assert repo.get_training_job(job) == result


def test_late_create_with_conflicting_payload_cannot_attach_identity(avatar_job):
    url, repo, _, job = avatar_job
    with psycopg.connect(url) as connection:
        connection.execute('UPDATE digital_human_training_jobs SET provider_job_id=NULL WHERE job_id=%s', (job,))
    repo.update_training_job(job, status='cancelled')
    with pytest.raises(StaleAvatarTrainingError):
        repo.update_training_job(job, status='training', provider_job_id='tavus:late-face',
            provider_payload={'face_id':'foreign'})
    assert repo.get_training_job(job)['provider_job_id'] is None


@pytest.mark.parametrize('revision', [1, 2])
def test_video_activation_requires_voice_grant_even_when_motion_is_allowed(avatar_job, revision):
    url, repo, profile, job = avatar_job
    with psycopg.connect(url) as connection:
        connection.execute('UPDATE digital_human_training_jobs SET request_payload=%s::jsonb WHERE job_id=%s',
            (json.dumps({'train_video_url':'https://stay.example/video', '_stay_consent_revision':1}), job))
        connection.execute('UPDATE profile_purpose_consents SET revision=%s, purposes=%s WHERE profile_id=%s',
            (revision, ['video_motion', 'provider_processing'], profile))
    before = repo.require(profile)
    result = apply(avatar_job, 'ready')
    assert result['status'] == 'ready' and not result['profile_updated']
    assert repo.require(profile) == before
    with pytest.raises(StaleAvatarTrainingError):
        repo.set_avatar_training(profile, provider='tavus', status='ready', provider_job_id='tavus:test-face',
            replica_id='test-face', training_job_id=job)
