from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.models.InAppOwnershipType import InAppOwnershipType
from appstoreserverlibrary.models.Type import Type
from appstoreserverlibrary.models.NotificationTypeV2 import NotificationTypeV2

from app.services.apple_purchase_verifier import (
    ApplePurchaseVerifier, InvalidPurchaseEvidence, SubscriptionProduct,
)


@pytest.fixture
def evidence(monkeypatch):
    account = uuid4()
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    clock = int(now.timestamp() * 1000)
    payload = SimpleNamespace(
        bundleId="test.stay", environment=Environment.PRODUCTION, productId="test.family.month",
        type=Type.AUTO_RENEWABLE_SUBSCRIPTION, inAppOwnershipType=InAppOwnershipType.PURCHASED,
        revocationDate=None, isUpgraded=False, appAccountToken=str(account),
        transactionId="123", originalTransactionId="100", purchaseDate=clock-1000,
        signedDate=clock, expiresDate=clock+100000)
    implementation = Mock()
    implementation.verify_and_decode_signed_transaction.return_value = payload
    factory = Mock(return_value=implementation)
    monkeypatch.setattr("app.services.apple_purchase_verifier.SignedDataVerifier", factory)
    verifier = ApplePurchaseVerifier(root_certificates=[b"test-root"], bundle_id="test.stay",
        app_apple_id=123, environment="Production",
        products={"test.family.month": SubscriptionProduct("family", "monthly")})
    return verifier, payload, account, now, factory


def test_verified_payload_preserves_account_and_product(evidence):
    verifier, payload, account, now, factory = evidence
    result = verifier.verify("signed-fixture", account_token=account, now=now)
    assert result.account_token == account
    assert result.product.plan == "family"
    assert result.transaction_id == "123"
    assert factory.call_args.args[1] is True  # Apple online certificate checks stay enabled.


def notification_fixture(evidence):
    verifier, payload, account, now, factory = evidence
    payload.revocationDate = payload.signedDate
    notification = SimpleNamespace(notificationType=NotificationTypeV2.REFUND, version="2.0",
        notificationUUID=str(uuid4()), signedDate=payload.signedDate,
        data=SimpleNamespace(bundleId="test.stay", environment=Environment.PRODUCTION,
            appAppleId=123, signedTransactionInfo="nested-signed-fixture"))
    verifier.verifier.verify_and_decode_notification.return_value = notification
    return verifier, payload, notification, now


def test_refund_checks_both_signatures_and_returns_no_entitlement(evidence):
    verifier, payload, notification, now = notification_fixture(evidence)
    result = verifier.verify_revocation("outer-signed-fixture", now=now)
    assert result.transaction_id == "123"
    assert result.revoked_at == now
    verifier.verifier.verify_and_decode_notification.assert_called_once_with("outer-signed-fixture")
    verifier.verifier.verify_and_decode_signed_transaction.assert_called_once_with("nested-signed-fixture")


@pytest.mark.parametrize("field,value", [("bundleId","other.app"), ("appAppleId",456),
                                         ("environment",Environment.SANDBOX)])
def test_foreign_refund_notification_is_rejected(evidence, field, value):
    verifier, payload, notification, now = notification_fixture(evidence)
    setattr(notification.data, field, value)
    with pytest.raises(InvalidPurchaseEvidence):
        verifier.verify_revocation("fixture", now=now)


@pytest.mark.parametrize("field,value", [("revocationDate",None), ("revocationDate",True),
    ("bundleId","other.app"), ("productId","unconfigured"), ("transactionId","")])
def test_bad_nested_refund_transaction_is_rejected(evidence, field, value):
    verifier, payload, notification, now = notification_fixture(evidence)
    setattr(payload, field, value)
    with pytest.raises(InvalidPurchaseEvidence):
        verifier.verify_revocation("fixture", now=now)


def test_refund_reversal_is_not_misinterpreted_as_a_new_refund(evidence):
    verifier, payload, notification, now = notification_fixture(evidence)
    notification.notificationType = NotificationTypeV2.REFUND_REVERSED
    with pytest.raises(InvalidPurchaseEvidence, match="reconciliation"):
        verifier.verify_revocation("fixture", now=now)


@pytest.mark.parametrize("field,value", [
    ("bundleId", "another.app"), ("environment", Environment.SANDBOX),
    ("productId", "unconfigured"), ("appAccountToken", str(uuid4())),
    ("appAccountToken", None), ("appAccountToken", "not-a-uuid"),
    ("inAppOwnershipType", InAppOwnershipType.FAMILY_SHARED),
    ("type", Type.CONSUMABLE), ("revocationDate", 1), ("isUpgraded", True),
    ("transactionId", ""), ("originalTransactionId", "user-input"),
    ("purchaseDate", True), ("expiresDate", 1), ("signedDate", 9999999999999),
])
def test_wrong_or_inactive_purchase_cannot_be_accepted(evidence, field, value):
    verifier, payload, account, now, _ = evidence
    setattr(payload, field, value)
    with pytest.raises(InvalidPurchaseEvidence):
        verifier.verify("signed-fixture", account_token=account, now=now)


def test_real_apple_library_rejects_unsigned_input():
    verifier = ApplePurchaseVerifier(root_certificates=[b"test-not-a-certificate"], bundle_id="test.stay",
        app_apple_id=123, environment="Production",
        products={"test.plus": SubscriptionProduct("plus", "monthly")})
    with pytest.raises(InvalidPurchaseEvidence):
        verifier.verify("not.a.valid-signature", account_token=uuid4(), now=datetime.now(timezone.utc))


@pytest.mark.parametrize("environment", ["Xcode", "LocalTesting", "production", ""])
def test_local_or_ambiguous_environments_are_not_purchase_evidence(environment):
    with pytest.raises(ValueError):
        ApplePurchaseVerifier(root_certificates=[b"test"], bundle_id="test.stay", app_apple_id=123,
            environment=environment, products={"test": SubscriptionProduct("plus", "annual")})
