"""Real concurrency acceptance for both owners of the existing credit ledger."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException

from tests.test_plan_access_postgres import principal
from app.services.family_repository import FamilyRepository
from app.services.family_credit_ledger import FamilyCreditLedger as Ledger, PersonalCreditAccount


@pytest.fixture(params=["personal", "family"])
def account(request, principal):
    repo = FamilyRepository()
    if request.param == "personal":
        token = uuid4()
        with repo.transaction(principal) as db:
            db.execute("INSERT INTO billing_accounts(account_token,user_id) VALUES (%s,%s)",
                       (token, principal.user.user_id))
        owner = PersonalCreditAccount(token)
    else:
        repo.create(principal, "Test family", "Tester")
        owner = repo.snapshot(principal)["family"]["family_id"]
    return repo, principal, owner


def test_parallel_calls_cannot_overspend_either_account(account):
    repo, person, owner = account
    with repo.transaction(person) as db:
        Ledger.grant(db, owner, evidence_key=str(uuid4()), units=60)
    def reserve(_):
        try:
            with repo.transaction(person) as db:
                Ledger.reserve(db, owner, person.user.user_id, uuid4(), mode="voice", seconds=60)
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert results.count(200) == 1
    assert results.count(409) == 7
    with repo.transaction(person) as db:
        assert Ledger.balance(db, owner)["available_units"] == 0


def test_retries_and_settlement_do_not_double_charge(account):
    repo, person, owner = account
    call = uuid4()
    with repo.transaction(person) as db:
        Ledger.grant(db, owner, evidence_key=str(uuid4()), units=60)
    for _ in range(2):
        with repo.transaction(person) as db:
            Ledger.reserve(db, owner, person.user.user_id, call, mode="voice", seconds=60)
    for _ in range(2):
        with repo.transaction(person) as db:
            Ledger.settle(db, owner, call, verified_seconds=7)
    with repo.transaction(person) as db:
        balance = Ledger.balance(db, owner)
        assert balance["balance_units"] == 53
        assert balance["reserved_units"] == 0


def test_empty_account_and_sandbox_grants_do_not_authorize_calls(account):
    repo, person, owner = account
    with repo.transaction(person) as db:
        Ledger.grant(db, owner, evidence_key=str(uuid4()), units=600, environment="Sandbox")
    with pytest.raises(HTTPException) as error:
        with repo.transaction(person) as db:
            Ledger.reserve(db, owner, person.user.user_id, uuid4(), mode="voice", seconds=1)
    assert error.value.status_code == 409


def test_foreign_personal_account_cannot_be_reserved(principal):
    repo = FamilyRepository()
    with repo.transaction(principal) as db:
        stranger, token = uuid4(), uuid4()
        db.execute("INSERT INTO users(user_id) VALUES (%s)", (stranger,))
        db.execute("INSERT INTO billing_accounts(account_token,user_id) VALUES (%s,%s)", (token,stranger))
        Ledger.grant(db, PersonalCreditAccount(token), evidence_key=str(uuid4()), units=600)
    with pytest.raises(HTTPException) as error:
        with repo.transaction(principal) as db:
            Ledger.reserve(db, PersonalCreditAccount(token), principal.user.user_id,
                           uuid4(), mode="voice", seconds=1)
    assert error.value.status_code == 403
