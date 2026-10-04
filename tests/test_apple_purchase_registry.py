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
            db.execute("DELETE FROM apple_revocation_notifications WHERE transaction_id=%s", (evidence.transaction_id,))
            db.execute("DELETE FROM apple_purchase_transactions WHERE original_transaction_id=%s", (evidence.original_transaction_id,))
            db.execute("DELETE FROM apple_subscription_ownership WHERE original_transaction_id=%s", (evidence.original_transaction_id,))
            db.execute("DELETE FROM apple_purchase_revocations WHERE transaction_id=%s", (evidence.transaction_id,))
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
