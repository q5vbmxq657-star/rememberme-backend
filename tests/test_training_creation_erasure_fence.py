from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from app.services.digital_human_profile_repository import DigitalHumanProfileRepositoryError
from test_avatar_activation_privacy import avatar_job


@pytest.mark.parametrize("kind,provider", [("avatar", "tavus"), ("voice", "elevenlabs")])
def test_training_creation_waits_for_erasure_and_cannot_escape_it(avatar_job, kind, provider):
    url, repository, profile, _ = avatar_job
    new_job = uuid4()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with psycopg.connect(url) as connection:
            connection.execute(
                "SELECT profile_id FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE", (profile,)
            )
            connection.execute(
                "INSERT INTO digital_human_profile_erasure_requests(request_id,profile_id,idempotency_key) VALUES(%s,%s,%s)",
                (uuid4(), profile, str(uuid4())),
            )
            future = executor.submit(
                repository.create_training_job, job_id=new_job, profile_id=profile,
                training_type=kind, provider=provider, status="created", training_version=2,
                idempotency_key=str(uuid4()), request_payload={},
            )
            connection.commit()
        with pytest.raises(DigitalHumanProfileRepositoryError):
            future.result(timeout=10)
    with psycopg.connect(url) as connection:
        assert connection.execute(
            "SELECT 1 FROM digital_human_training_jobs WHERE job_id=%s", (new_job,)
        ).fetchone() is None
