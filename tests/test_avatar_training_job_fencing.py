from unittest.mock import MagicMock
from uuid import uuid4

import app.services.digital_human_profile_repository as repository_module


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
    repository, cursor, connection = repository_with_rows(monkeypatch, [row])
    result = repository.set_avatar_training(
        profile_id, provider="tavus", status="ready", provider_job_id="tavus:current",
        replica_id="current", expected_provider_job_id="tavus:current",
    )
    sql, parameters = cursor.execute.call_args.args
    assert "AND (%s::text IS NULL OR avatar_training_job_id = %s)" in sql
    assert parameters[-3:] == (profile_id, "tavus:current", "tavus:current")
    assert result == row
    connection.commit.assert_called_once()


def test_rejected_old_job_returns_current_profile_without_retrying_update(monkeypatch):
    profile_id = uuid4()
    row = {"profile_id": profile_id, "avatar_training_job_id": "tavus:new", "avatar_training_status": "training"}
    repository, cursor, connection = repository_with_rows(monkeypatch, [None, row])
    result = repository.set_avatar_training(
        profile_id, provider="tavus", status="ready", provider_job_id="tavus:old",
        replica_id="old", expected_provider_job_id="tavus:old",
    )
    assert result == row
    assert cursor.execute.call_count == 2
    sql, parameters = cursor.execute.call_args.args
    assert sql.startswith("SELECT")
    assert parameters == (profile_id,)
    connection.commit.assert_called_once()


def test_new_training_assignment_still_uses_existing_unconditional_contract(monkeypatch):
    row = {"profile_id": uuid4(), "avatar_training_job_id": "tavus:new"}
    repository, cursor, _ = repository_with_rows(monkeypatch, [row])
    result = repository.set_avatar_training(
        row["profile_id"], provider="tavus", status="training", provider_job_id="tavus:new",
    )
    assert result == row
    assert cursor.execute.call_args.args[1][-2:] == (None, None)
