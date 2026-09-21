from unittest.mock import MagicMock
from uuid import uuid4
import pytest
from app.schemas.profile_consent import CONSENT_POLICY_VERSION

import app.services.digital_human_profile_repository as repository_module


def job(identifier):
    return {'job_id': uuid4(), 'provider_job_id': identifier,
        'request_payload': {'train_image_url': 'https://stay.example/portrait', '_stay_consent_revision': 7}}


def consent():
    return {'revision': 7, 'policy_version': CONSENT_POLICY_VERSION,
        'purposes': ['photo_likeness', 'provider_processing']}


def repository_with_rows(monkeypatch, rows):
    cursor = MagicMock()
    cursor.fetchone.side_effect = rows
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    connect = MagicMock()
    connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(repository_module.psycopg, "connect", connect)
    repository = object.__new__(repository_module.DigitalHumanProfileRepository)
    repository.database_url = "unused-test-database"
    repository._profile_from_row = lambda row: row
    return repository, cursor, connection


def test_current_job_guard_is_part_of_the_atomic_update(monkeypatch):
    profile_id = uuid4()
    row = {"profile_id": profile_id, "avatar_training_job_id": "tavus:current", "avatar_training_status": "ready"}
    repository, cursor, connection = repository_with_rows(monkeypatch, [row, None, job('tavus:current'), consent(), row])
    result = repository.set_avatar_training(
        profile_id, provider="tavus", status="ready", provider_job_id="tavus:current",
        replica_id="current", expected_provider_job_id="tavus:current",
    )
    sql, parameters = cursor.execute.call_args.args
    assert "AND (%s::text IS NULL OR avatar_training_job_id = %s)" in sql
    assert parameters[-3:] == (profile_id, "tavus:current", "tavus:current")
    assert result == row
    assert 'FOR UPDATE' in cursor.execute.call_args_list[0].args[0]
    connection.commit.assert_called_once()


def test_rejected_old_job_raises_without_any_profile_update(monkeypatch):
    profile_id = uuid4()
    row = {"profile_id": profile_id, "avatar_training_job_id": "tavus:new", "avatar_training_status": "training"}
    repository, cursor, connection = repository_with_rows(monkeypatch, [row, None, job('tavus:new')])
    with pytest.raises(repository_module.StaleAvatarTrainingError):
        repository.set_avatar_training(
            profile_id, provider="tavus", status="ready", provider_job_id="tavus:old",
            replica_id="old", expected_provider_job_id="tavus:old",
        )
    assert all(not call.args[0].lstrip().startswith('UPDATE') for call in cursor.execute.call_args_list)
    connection.commit.assert_not_called()


def test_new_training_assignment_requires_latest_job_and_valid_purpose_revision(monkeypatch):
    row = {"profile_id": uuid4(), "avatar_training_job_id": "tavus:new"}
    training_job = job('tavus:new')
    repository, cursor, _ = repository_with_rows(monkeypatch, [row, None, training_job, consent(), row])
    result = repository.set_avatar_training(
        row["profile_id"], provider="tavus", status="training", provider_job_id="tavus:new",
        training_job_id=training_job['job_id'],
    )
    assert result == row
    assert cursor.execute.call_args.args[1][-2:] == (None, None)
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert 'FOR UPDATE' in statements[0]
    assert 'digital_human_profile_erasure_requests' in statements[1]
    assert 'digital_human_training_jobs' in statements[2]
    assert 'profile_purpose_consents' in statements[3]
