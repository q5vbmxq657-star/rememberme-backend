from uuid import uuid4

import psycopg
import pytest

from test_avatar_activation_privacy import avatar_job


@pytest.mark.parametrize('status', ['created', 'submitted', 'training', 'ready', 'cancelled', 'deleted'])
def test_lookup_returns_exact_durable_request_without_changes(avatar_job, status):
    url, repo, profile, job = avatar_job
    with psycopg.connect(url) as connection:
        connection.execute('UPDATE digital_human_training_jobs SET status=%s WHERE job_id=%s', (status, job))
    before = repo.get_training_job(job)
    assert repo.get_avatar_training_request(profile, before['idempotency_key']) == before
    assert repo.get_training_job(job) == before


@pytest.mark.parametrize('mismatch', ['profile', 'key', 'provider', 'type'])
def test_lookup_never_returns_foreign_or_unrelated_request(avatar_job, mismatch):
    url, repo, profile, job = avatar_job
    if mismatch in {'provider', 'type'}:
        with psycopg.connect(url) as connection:
            if mismatch == 'provider':
                connection.execute("UPDATE digital_human_training_jobs SET provider='other' WHERE job_id=%s", (job,))
            else:
                connection.execute("UPDATE digital_human_training_jobs SET training_type='voice' WHERE job_id=%s", (job,))
    before = repo.get_training_job(job)
    result = repo.get_avatar_training_request(uuid4() if mismatch == 'profile' else profile,
        str(uuid4()) if mismatch == 'key' else before['idempotency_key'])
    assert result is None
    assert repo.get_training_job(job) == before
