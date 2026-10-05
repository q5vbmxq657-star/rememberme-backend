from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi import HTTPException

from test_family_membership import family_data
from app.services.apple_purchase_registry import ApplePurchaseRegistry as Registry
from app.services.apple_purchase_verifier import VerifiedSubscriptionEvidence, SubscriptionProduct, VerifiedPurchaseRevocation


@pytest.fixture
def purchase(family_data):
    repo, people, url = family_data
    user = people[0].user.user_id
    with repo.transaction(people[0]) as db:
        token = Registry.account_token(db, user)
    now = datetime.now(timezone.utc)
    evidence = VerifiedSubscriptionEvidence(str(uuid4().int), str(uuid4().int), token,
        "Sandbox", "test.family.month", SubscriptionProduct("family","monthly"), now, now+timedelta(days=30))
    try:
        yield repo, people, url, evidence
    finally:
        with psycopg.connect(url) as db:
            db.execute("DELETE FROM apple_family_credit_allocations WHERE transaction_id=%s", (evidence.transaction_id,))
            db.execute("DELETE FROM apple_family_credit_bindings WHERE original_transaction_id=%s", (evidence.original_transaction_id,))
            db.execute("DELETE FROM apple_revocation_notifications WHERE transaction_id=%s", (evidence.transaction_id,))
            db.execute("DELETE FROM apple_purchase_transactions WHERE original_transaction_id=%s", (evidence.original_transaction_id,))
            db.execute("DELETE FROM apple_subscription_ownership WHERE original_transaction_id=%s", (evidence.original_transaction_id,))
            db.execute("DELETE FROM apple_purchase_revocations WHERE transaction_id=%s", (evidence.transaction_id,))
            db.execute("DELETE FROM family_credit_entries WHERE account_token=%s", (token,))
            db.execute("DELETE FROM billing_accounts WHERE account_token=%s OR user_id=ANY(%s)",
                (token, [p.user.user_id for p in people]))


def test_repeated_purchase_has_one_durable_owner(purchase):
    repo, people, url, evidence = purchase
    for _ in range(2):
        with repo.transaction(people[0]) as db:
            row = Registry.record(db, people[0].user.user_id, evidence)
            assert row["revoked_at"] is None
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM apple_purchase_transactions WHERE transaction_id=%s",
                          (evidence.transaction_id,)).fetchone()[0] == 1


def test_notification_receiver_commits_revocation_before_success(purchase, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from app.routes import billing
    from appstoreserverlibrary.models.NotificationTypeV2 import NotificationTypeV2
    repo, people, url, evidence = purchase
    event = VerifiedPurchaseRevocation(uuid4(), evidence.environment, evidence.transaction_id, evidence.paid_from)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(billing, "configured_verifier", lambda: SimpleNamespace(
        verify_notification=lambda *a, **k: SimpleNamespace(notificationType=NotificationTypeV2.REFUND),
        verify_revocation=lambda *a, **k: event))
    app = FastAPI()
    app.include_router(billing.router)
    client = TestClient(app)
    for _ in range(2):
        assert client.post("/v1/billing/apple/notifications", json={"signedPayload": "verified-fixture"}).status_code == 200
    with repo.transaction(people[0]) as db:
        assert Registry.record(db, people[0].user.user_id, evidence)["revoked_at"] == evidence.paid_from
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM apple_revocation_notifications WHERE notification_id=%s",
                          (event.notification_id,)).fetchone()[0] == 1


def test_family_fulfillment_and_refund_are_atomic_and_environment_isolated(purchase):
    from app.services.apple_credit_fulfillment import fulfill_subscription_purchase
    from app.services.family_credit_ledger import FamilyCreditLedger
    repo, people, url, evidence = purchase
    owner = people[0]
    repo.create(owner, "Family", "Owner")
    fid = repo.snapshot(owner)["family"]["family_id"]
    for _ in range(2):
        with repo.transaction(owner) as db:
            Registry.record(db, owner.user.user_id, evidence)
            result = fulfill_subscription_purchase(db, owner.user.user_id, evidence, now=evidence.paid_from)
            assert result["balance_units"] == 18000
    # Sandbox evidence must never fund production calls.
    assert repo.credits(owner)["available_units"] == 0
    for _ in range(2):
        with repo.transaction(owner) as db:
            Registry.revoke(db, environment=evidence.environment, transaction_id=evidence.transaction_id,
                            revoked_at=evidence.paid_from)
            assert FamilyCreditLedger.balance(db, fid, environment="Sandbox")["balance_units"] == 0
    with pytest.raises(HTTPException):
        with repo.transaction(owner) as db:
            Registry.record(db, owner.user.user_id, evidence)
            fulfill_subscription_purchase(db, owner.user.user_id, evidence, now=evidence.paid_from)


def test_family_refund_after_usage_preserves_accounting_debt(family_data):
    from app.services.family_credit_ledger import FamilyCreditLedger as Ledger
    repo, people, _ = family_data
    owner = people[0]
    repo.create(owner, "Family", "Owner")
    fid = repo.snapshot(owner)["family"]["family_id"]
    call = uuid4()
    with repo.transaction(owner) as db:
        key = str(uuid4())
        Ledger.grant(db, fid, evidence_key=key, units=600)
        entry = db.execute("SELECT entry_id FROM family_credit_entries WHERE evidence_key=%s", (key,)).fetchone()
        Ledger.reserve(db, fid, owner.user.user_id, call, mode="voice", seconds=60)
        Ledger.settle(db, fid, call, verified_seconds=60)
        for _ in range(2):
            result = Ledger.refund_grant(db, fid, entry["entry_id"])
            assert result["balance_units"] == -60
            assert result["available_units"] == 0
        with pytest.raises(HTTPException):
            Ledger.reserve(db, fid, owner.user.user_id, uuid4(), mode="voice", seconds=1)


def test_deleted_family_cannot_reuse_a_previously_funded_subscription(purchase):
    from app.services.apple_credit_fulfillment import fulfill_subscription_purchase
    repo, people, url, evidence = purchase
    owner = people[0]
    repo.create(owner, "Original family", "Owner")
    fid = repo.snapshot(owner)["family"]["family_id"]
    with repo.transaction(owner) as db:
        Registry.record(db, owner.user.user_id, evidence)
        fulfill_subscription_purchase(db, owner.user.user_id, evidence, now=evidence.paid_from)
    with psycopg.connect(url) as db:
        db.execute("DELETE FROM family_groups WHERE family_id=%s", (fid,))
    repo.create(owner, "Replacement family", "Owner")
    with pytest.raises(HTTPException):
        with repo.transaction(owner) as db:
            Registry.record(db, owner.user.user_id, evidence)
            fulfill_subscription_purchase(db, owner.user.user_id, evidence, now=evidence.paid_from)
    assert repo.credits(owner)["available_units"] == 0


def test_concurrent_family_restores_grant_once(purchase):
    from app.services.apple_credit_fulfillment import fulfill_subscription_purchase
    repo, people, url, evidence = purchase
    owner = people[0]
    repo.create(owner, "Family", "Owner")
    def restore(_):
        with repo.transaction(owner) as db:
            Registry.record(db, owner.user.user_id, evidence)
            return fulfill_subscription_purchase(db, owner.user.user_id, evidence, now=evidence.paid_from)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(restore, range(4)))
    assert all(result["balance_units"] == 18000 for result in results)


def test_annual_purchase_only_funds_elapsed_months(purchase):
    from app.services.apple_credit_fulfillment import fulfill_subscription_purchase
    repo, people, url, original = purchase
    owner = people[0]
    repo.create(owner, "Family", "Owner")
    start = datetime(2026, 1, 31, tzinfo=timezone.utc)
    evidence = replace(original, product=SubscriptionProduct("family", "annual"),
                       paid_from=start, paid_until=datetime(2027, 1, 31, tzinfo=timezone.utc))
    with repo.transaction(owner) as db:
        Registry.record(db, owner.user.user_id, evidence)
        assert fulfill_subscription_purchase(db, owner.user.user_id, evidence, now=start)["balance_units"] == 18000
    for _ in range(2):
        with repo.transaction(owner) as db:
            result = fulfill_subscription_purchase(db, owner.user.user_id, evidence,
                                            now=datetime(2026, 3, 31, tzinfo=timezone.utc))
            assert result["balance_units"] == 54000


def test_plus_purchase_uses_personal_ledger_and_refunds_once(purchase):
    from app.services.apple_credit_fulfillment import fulfill_subscription_purchase
    from app.services.family_credit_ledger import FamilyCreditLedger as Ledger, PersonalCreditAccount
    repo, people, _, original = purchase
    evidence = replace(original, product=SubscriptionProduct("plus", "monthly"))
    owner = people[0]
    for _ in range(2):
        with repo.transaction(owner) as db:
            Registry.record(db, owner.user.user_id, evidence)
            result = fulfill_subscription_purchase(db, owner.user.user_id, evidence, now=evidence.paid_from)
            assert result["balance_units"] == 6000
            assert Ledger.balance(db, PersonalCreditAccount(evidence.account_token))["balance_units"] == 0
    for _ in range(2):
        with repo.transaction(owner) as db:
            Registry.revoke(db, environment=evidence.environment, transaction_id=evidence.transaction_id,
                            revoked_at=evidence.paid_from)
            assert Ledger.balance(db, PersonalCreditAccount(evidence.account_token),
                                  environment="Sandbox")["balance_units"] == 0


def test_plus_credits_cannot_be_claimed_by_another_user(purchase):
    from app.services.apple_credit_fulfillment import fulfill_subscription_purchase
    repo, people, _, original = purchase
    evidence = replace(original, product=SubscriptionProduct("plus", "monthly"))
    with repo.transaction(people[0]) as db:
        Registry.record(db, people[0].user.user_id, evidence)
    with pytest.raises(HTTPException):
        with repo.transaction(people[1]) as db:
            fulfill_subscription_purchase(db, people[1].user.user_id, evidence, now=evidence.paid_from)


def test_server_purchase_notification_grants_without_app_session(purchase, monkeypatch):
    from app.routes.billing import record_notification_purchase
    from app.services.family_credit_ledger import FamilyCreditLedger as Ledger, PersonalCreditAccount
    repo, people, url, original = purchase
    evidence = replace(original, product=SubscriptionProduct("plus", "monthly"))
    monkeypatch.setenv("DATABASE_URL", url)
    for _ in range(2):
        record_notification_purchase(evidence, now=evidence.paid_from)
    with repo.transaction(people[0]) as db:
        assert Ledger.balance(db, PersonalCreditAccount(evidence.account_token),
                              environment="Sandbox")["balance_units"] == 6000


def test_annual_scheduler_catches_up_and_defers_next_check(purchase):
    from types import SimpleNamespace
    from appstoreserverlibrary.models.Environment import Environment
    from app.services.apple_allowance_scheduler import AppleAllowanceScheduler
    from app.services.family_credit_ledger import FamilyCreditLedger as Ledger, PersonalCreditAccount
    repo, people, url, original = purchase
    now = datetime.now(timezone.utc)
    evidence = replace(original, product=SubscriptionProduct("plus", "annual"),
        paid_from=now-timedelta(days=65), paid_until=now+timedelta(days=200))
    with repo.transaction(people[0]) as db:
        Registry.record(db, people[0].user.user_id, evidence)
    fetched = []
    def fetch(identity):
        fetched.append(identity)
        return SimpleNamespace(signedTransactionInfo="verified-current")
    worker = AppleAllowanceScheduler(url,
        SimpleNamespace(environment=Environment.SANDBOX, verify=lambda *a, **k: evidence),
        SimpleNamespace(get_transaction_info=fetch))
    assert worker.run_once() is True
    assert worker.run_once() is False
    assert fetched == [evidence.transaction_id]
    with repo.transaction(people[0]) as db:
        assert Ledger.balance(db, PersonalCreditAccount(evidence.account_token),
                              environment="Sandbox")["balance_units"] == 18000


def test_annual_scheduler_failure_keeps_retry_and_grants_nothing(purchase):
    from types import SimpleNamespace
    from appstoreserverlibrary.models.Environment import Environment
    from app.services.apple_allowance_scheduler import AppleAllowanceScheduler
    repo, people, url, original = purchase
    evidence = replace(original, product=SubscriptionProduct("plus", "annual"))
    with repo.transaction(people[0]) as db:
        Registry.record(db, people[0].user.user_id, evidence)
    def fail(_):
        raise RuntimeError("Provider unavailable")
    worker = AppleAllowanceScheduler(url, SimpleNamespace(environment=Environment.SANDBOX),
                                    SimpleNamespace(get_transaction_info=fail))
    with pytest.raises(RuntimeError):
        worker.run_once()
    with psycopg.connect(url) as db:
        retry = db.execute("""SELECT allowance_next_check_at>NOW(),allowance_lease IS NULL
            FROM apple_purchase_transactions WHERE environment=%s AND transaction_id=%s""",
            (evidence.environment,evidence.transaction_id)).fetchone()
        assert retry == (True, True)
        assert db.execute("SELECT count(*) FROM apple_family_credit_allocations WHERE transaction_id=%s",
                          (evidence.transaction_id,)).fetchone()[0] == 0


def test_notification_replay_is_atomic_and_conflicting_reuse_is_rejected(purchase):
    repo, people, url, evidence = purchase
    event = VerifiedPurchaseRevocation(uuid4(), evidence.environment, evidence.transaction_id, evidence.paid_from)
    for _ in range(2):
        with repo.transaction(people[0]) as db:
            Registry.record_revocation(db, event)
    with pytest.raises(HTTPException):
        with repo.transaction(people[0]) as db:
            Registry.record_revocation(db, replace(event, revoked_at=event.revoked_at+timedelta(seconds=1)))
    with repo.transaction(people[0]) as db:
        assert Registry.record(db, people[0].user.user_id, evidence)["revoked_at"] == evidence.paid_from


def test_simultaneous_restore_and_refund_remain_revoked(purchase):
    repo, people, url, evidence = purchase

    def execute(refund):
        with psycopg.connect(url, row_factory=dict_row) as db:
            if refund:
                Registry.revoke(db, environment=evidence.environment,
                    transaction_id=evidence.transaction_id, revoked_at=evidence.paid_from)
            else:
                Registry.record(db, people[0].user.user_id, evidence)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(execute, [False, True, False, True]))
    with psycopg.connect(url, row_factory=dict_row) as db:
        row = db.execute("SELECT revoked_at FROM apple_purchase_transactions WHERE environment=%s AND transaction_id=%s",
                         (evidence.environment,evidence.transaction_id)).fetchone()
        assert row["revoked_at"] == evidence.paid_from


def test_account_token_creation_is_stable_under_concurrency(purchase):
    repo, people, url, evidence = purchase

    def token(_):
        with psycopg.connect(url, row_factory=dict_row) as db:
            return Registry.account_token(db, people[1].user.user_id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(token, range(4)))
    assert len(set(tokens)) == 1
    assert tokens[0] != evidence.account_token


def test_other_account_cannot_claim_original_subscription(purchase):
    repo, people, url, evidence = purchase
    with repo.transaction(people[0]) as db:
        Registry.record(db, people[0].user.user_id, evidence)
    with repo.transaction(people[1]) as db:
        token = Registry.account_token(db, people[1].user.user_id)
    with pytest.raises(HTTPException) as error:
        with repo.transaction(people[1]) as db:
            Registry.record(db, people[1].user.user_id, replace(evidence, account_token=token))
    assert error.value.status_code == 409


@pytest.mark.parametrize("refund_first", [False, True])
def test_refund_cannot_be_undone_by_late_purchase_replay(purchase, refund_first):
    repo, people, url, evidence = purchase
    if not refund_first:
        with repo.transaction(people[0]) as db:
            Registry.record(db, people[0].user.user_id, evidence)
    with repo.transaction(people[0]) as db:
        Registry.revoke(db, environment=evidence.environment, transaction_id=evidence.transaction_id,
                        revoked_at=evidence.paid_from)
    for _ in range(2):
        with repo.transaction(people[0]) as db:
            row = Registry.record(db, people[0].user.user_id, evidence)
            assert row["revoked_at"] == evidence.paid_from


def test_conflicting_receipt_does_not_overwrite_original(purchase):
    repo, people, url, evidence = purchase
    with repo.transaction(people[0]) as db:
        Registry.record(db, people[0].user.user_id, evidence)
    with pytest.raises(HTTPException) as error:
        with repo.transaction(people[0]) as db:
            Registry.record(db, people[0].user.user_id,
                            replace(evidence, paid_until=evidence.paid_until+timedelta(days=1)))
    assert error.value.status_code == 409


def test_deleted_account_does_not_make_its_purchase_claimable(purchase):
    repo, people, url, evidence = purchase
    with repo.transaction(people[0]) as db:
        Registry.record(db, people[0].user.user_id, evidence)
    with psycopg.connect(url) as db:
        db.execute("DELETE FROM users WHERE user_id=%s", (people[0].user.user_id,))
    with psycopg.connect(url, row_factory=dict_row) as db:
        row = db.execute("SELECT user_id FROM billing_accounts WHERE account_token=%s", (evidence.account_token,)).fetchone()
        assert row["user_id"] is None
    with repo.transaction(people[1]) as db:
        token = Registry.account_token(db, people[1].user.user_id)
    with pytest.raises(HTTPException):
        with repo.transaction(people[1]) as db:
            Registry.record(db, people[1].user.user_id, replace(evidence, account_token=token))
