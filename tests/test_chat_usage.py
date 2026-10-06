from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from app.services import chat_usage


def setup(monkeypatch, *, used=0, duplicate=False):
    db = Mock()
    db.execute.return_value.fetchone.side_effect = [None, {"found": 1} if duplicate else None,
                                                   {"start": "2026-10-05"}, {"used": used}]
    @contextmanager
    def transaction(principal):
        yield db
    monkeypatch.setattr(chat_usage, "FamilyRepository", lambda: SimpleNamespace(transaction=transaction))
    monkeypatch.setattr(chat_usage, "effective_plan", lambda *args: "free")
    principal = SimpleNamespace(user=SimpleNamespace(user_id=uuid4()))
    return chat_usage.ChatUsage(principal, uuid4()), db


def test_last_slot_is_reserved_under_account_lock(monkeypatch):
    usage, db = setup(monkeypatch, used=9)
    # The advisory-lock execute does not fetch a row.
    db.execute.return_value.fetchone.side_effect = [None, {"start": "2026-10-05"}, {"used": 9}]
    usage.reserve()
    queries = [call.args[0] for call in db.execute.call_args_list]
    assert "pg_advisory_xact_lock" in queries[0]
    assert "INSERT INTO chat_weekly_usage" in queries[-1]
    assert "AT TIME ZONE 'UTC'" in queries[2]


def test_eleventh_message_is_not_inserted(monkeypatch):
    usage, db = setup(monkeypatch)
    db.execute.return_value.fetchone.side_effect = [None, {"start": "2026-10-05"}, {"used": 10}]
    with pytest.raises(HTTPException) as error:
        usage.reserve()
    assert error.value.status_code == 402
    assert error.value.detail["reason"] == "weekly_chat_limit"
    assert not any("INSERT" in call.args[0] for call in db.execute.call_args_list)


def test_duplicate_cannot_start_another_provider_request(monkeypatch):
    usage, db = setup(monkeypatch)
    db.execute.return_value.fetchone.side_effect = [{"found": 1}]
    with pytest.raises(HTTPException) as error:
        usage.reserve()
    assert error.value.status_code == 409


def test_failure_does_not_remove_completed_usage(monkeypatch):
    usage, db = setup(monkeypatch)
    usage.finish(completed=False)
    assert "AND NOT completed" in db.execute.call_args.args[0]
