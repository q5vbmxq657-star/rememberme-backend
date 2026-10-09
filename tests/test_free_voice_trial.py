from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock

import pytest
from app.services import free_voice_trial as trial


def test_allowance_has_stable_account_identity(monkeypatch):
    uid, token = uuid4(), uuid4()
    monkeypatch.setattr(trial, "effective_plan", lambda db, user: "free")
    monkeypatch.setattr(trial.ApplePurchaseRegistry, "account_token", lambda db, user: token)
    grant = Mock()
    monkeypatch.setattr(trial.FamilyCreditLedger, "grant", grant)
    db = object()
    trial.grant_free_voice_trial(db, uid)
    trial.grant_free_voice_trial(db, uid)
    assert grant.call_args_list[0] == grant.call_args_list[1]
    assert grant.call_args.kwargs == {"evidence_key": f"free-voice-trial:v1:{uid}", "units": 300}


@pytest.mark.parametrize("plan", ["plus", "family"])
def test_paid_plan_does_not_receive_trial(monkeypatch, plan):
    monkeypatch.setattr(trial, "effective_plan", lambda db, user: plan)
    grant = Mock()
    monkeypatch.setattr(trial.FamilyCreditLedger, "grant", grant)
    trial.grant_free_voice_trial(object(), uuid4())
    grant.assert_not_called()


@pytest.mark.parametrize("plan,expected", [("free", "generic"), ("plus", "trained"), ("family", "trained")])
def test_voice_choice_uses_server_plan(monkeypatch, plan, expected):
    @contextmanager
    def transaction(self, principal):
        yield object()
    monkeypatch.setenv("DATABASE_URL", "test")
    monkeypatch.setattr("app.services.family_repository.FamilyRepository.transaction", transaction)
    monkeypatch.setattr(trial, "effective_plan", lambda db, user: plan)
    principal = SimpleNamespace(user=SimpleNamespace(user_id=uuid4()))
    assert trial.voice_version_for_account(principal, "trained") == expected
