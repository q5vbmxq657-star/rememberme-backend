from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from app.services.digital_human_profile_repository import DigitalHumanProfileRepositoryError
from test_avatar_activation_privacy import avatar_job


def unattempted(fixture):
    url, _, _, job = fixture
    with psycopg.connect(url) as connection:
        connection.execute("UPDATE digital_human_training_jobs SET status='created', provider_job_id=NULL, submitted_at=NULL WHERE job_id=%s", (job,))


def test_concurrent_claim_has_exactly_one_winner(avatar_job):
    _, repo, profile, job = avatar_job
    unattempted(avatar_job)
    barrier = Barrier(6)
    def claim():
        barrier.wait(timeout=5)
        return repo.claim_avatar_submission(job, profile)
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(claim) for _ in range(6)]
        results = [future.result(timeout=15) for future in futures]
    assert results.count(True) == 1 and results.count(False) == 5
    row = repo.get_training_job(job)
    assert row['status'] == 'submitted' and row['submitted_at'] is not None
    assert row['provider_job_id'] is None
    assert not repo.claim_avatar_submission(job, profile)
    assert repo.get_training_job(job) == row


@pytest.mark.parametrize('status', ['submitted', 'training', 'ready', 'failed', 'cancelled', 'deleted'])
def test_claim_preserves_attempted_and_terminal_jobs(avatar_job, status):
    url, repo, profile, job = avatar_job
    with psycopg.connect(url) as connection:
        connection.execute('UPDATE digital_human_training_jobs SET status=%s,provider_job_id=NULL WHERE job_id=%s', (status, job))
    before = repo.get_training_job(job)
    assert not repo.claim_avatar_submission(job, profile)
    assert repo.get_training_job(job) == before


@pytest.mark.parametrize('mutation', ['known_provider_id', 'voice', 'other_provider', 'foreign_profile', 'missing_job'])
def test_claim_requires_owned_unattempted_avatar(avatar_job, mutation):
    url, repo, profile, job = avatar_job
    unattempted(avatar_job)
    if mutation in {'known_provider_id', 'voice', 'other_provider'}:
        with psycopg.connect(url) as connection:
            if mutation == 'known_provider_id':
                connection.execute("UPDATE digital_human_training_jobs SET provider_job_id='tavus:existing' WHERE job_id=%s", (job,))
            elif mutation == 'voice':
                connection.execute("UPDATE digital_human_training_jobs SET training_type='voice' WHERE job_id=%s", (job,))
            else:
                connection.execute("UPDATE digital_human_training_jobs SET provider='other' WHERE job_id=%s", (job,))
    before = repo.get_training_job(job)
    assert not repo.claim_avatar_submission(uuid4() if mutation == 'missing_job' else job,
        uuid4() if mutation == 'foreign_profile' else profile)
    assert repo.get_training_job(job) == before


def test_existing_foreign_profile_cannot_claim_job(avatar_job):
    url, repo, _, job = avatar_job
    unattempted(avatar_job)
    other_profile = uuid4()
    repo.ensure(other_profile)
    try:
        before = repo.get_training_job(job)
        assert not repo.claim_avatar_submission(job, other_profile)
        assert repo.get_training_job(job) == before
    finally:
        with psycopg.connect(url) as connection:
            connection.execute('DELETE FROM digital_human_profiles WHERE profile_id=%s', (other_profile,))


def test_active_erasure_prevents_claim(avatar_job):
    _, repo, profile, job = avatar_job
    unattempted(avatar_job)
    repo.create_profile_erasure_request(request_id=uuid4(), profile_id=profile, idempotency_key=str(uuid4()))
    before = repo.get_training_job(job)
    with pytest.raises(DigitalHumanProfileRepositoryError):
        repo.claim_avatar_submission(job, profile)
    assert repo.get_training_job(job) == before
