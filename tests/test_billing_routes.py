from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.routes import billing
from app.security.user_auth import require_authenticated_principal
from app.services.apple_purchase_verifier import VerifiedSubscriptionEvidence, SubscriptionProduct
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.models.Status import Status
from appstoreserverlibrary.models.NotificationTypeV2 import NotificationTypeV2
from fastapi import HTTPException
import requests


@pytest.mark.parametrize("source", ["environment", "file"])
def test_status_client_accepts_valid_server_key(monkeypatch, tmp_path, source):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    monkeypatch.setenv("STAY_APPLE_KEY_ID", "test-key")
    monkeypatch.setenv("STAY_APPLE_ISSUER_ID", "test-issuer")
    monkeypatch.delenv("STAY_APPLE_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("STAY_APPLE_PRIVATE_KEY", raising=False)
    if source == "file":
        path = tmp_path / "key.p8"
        path.write_bytes(pem)
        monkeypatch.setenv("STAY_APPLE_PRIVATE_KEY_PATH", str(path))
    else:
        monkeypatch.setenv("STAY_APPLE_PRIVATE_KEY", pem.decode())
    monkeypatch.setattr(billing, "AppStoreServerAPIClient", lambda *args: args)
    result = billing.configured_status_client(SimpleNamespace(bundle_id="app.stay", environment="Sandbox"))
    assert result[0] == pem.strip() if source == "environment" else result[0] == pem


@pytest.mark.parametrize("pem,path", [("", ""), ("invalid-secret", ""), ("secret", "/unused")])
def test_status_client_rejects_missing_invalid_or_ambiguous_key(monkeypatch, pem, path):
    monkeypatch.setenv("STAY_APPLE_KEY_ID", "test-key")
    monkeypatch.setenv("STAY_APPLE_ISSUER_ID", "test-issuer")
    monkeypatch.setenv("STAY_APPLE_PRIVATE_KEY", pem)
    monkeypatch.setenv("STAY_APPLE_PRIVATE_KEY_PATH", path)
    with pytest.raises(HTTPException) as error:
        billing.configured_status_client(SimpleNamespace(bundle_id="app.stay", environment="Sandbox"))
    assert error.value.status_code == 503
    assert "secret" not in error.value.detail


@pytest.mark.parametrize("kind", [NotificationTypeV2.TEST, NotificationTypeV2.REFUND, NotificationTypeV2.REVOKE])
def test_signed_notification_is_handled_without_user_auth(monkeypatch, kind):
    app = FastAPI()
    app.include_router(billing.router)
    evidence = object()
    calls = []
    monkeypatch.setattr(billing, "configured_verifier", lambda: SimpleNamespace(
        verify_notification=lambda *a, **k: SimpleNamespace(notificationType=kind),
        verify_revocation=lambda *a, **k: evidence))
    monkeypatch.setattr(billing, "record_notification_revocation", lambda value: calls.append(value))
    response = TestClient(app).post("/v1/billing/apple/notifications", json={"signedPayload": "signed"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert calls == ([] if kind == NotificationTypeV2.TEST else [evidence])


def test_notification_bad_signature_never_reaches_storage(client, monkeypatch):
    def reject(*args, **kwargs):
        raise billing.InvalidPurchaseEvidence()
    monkeypatch.setattr(billing, "configured_verifier", lambda: SimpleNamespace(verify_notification=reject))
    monkeypatch.setattr(billing, "record_notification_revocation", lambda _: pytest.fail("unverified write"))
    response = client.post("/v1/billing/apple/notifications", json={"signedPayload": "forged"})
    assert response.status_code == 422


@pytest.mark.parametrize("kind", [NotificationTypeV2.DID_FAIL_TO_RENEW, NotificationTypeV2.REFUND_REVERSED])
def test_unprocessed_notifications_are_not_silently_acknowledged(client, monkeypatch, kind):
    monkeypatch.setattr(billing, "configured_verifier", lambda: SimpleNamespace(
        verify_notification=lambda *a, **k: SimpleNamespace(notificationType=kind)))
    response = client.post("/v1/billing/apple/notifications", json={"signedPayload": "signed"})
    assert response.status_code == 503


def test_notification_commit_failure_requests_retry(client, monkeypatch):
    monkeypatch.setattr(billing, "configured_verifier", lambda: SimpleNamespace(
        verify_notification=lambda *a, **k: SimpleNamespace(notificationType=NotificationTypeV2.REFUND),
        verify_revocation=lambda *a, **k: object()))
    def fail(_):
        raise billing.psycopg.OperationalError("private database details")
    monkeypatch.setattr(billing, "record_notification_revocation", fail)
    response = client.post("/v1/billing/apple/notifications", json={"signedPayload": "signed"})
    assert response.status_code == 503
    assert "private" not in response.text


def test_notification_payload_is_bounded(client):
    assert client.post("/v1/billing/apple/notifications", json={"signedPayload": "a" * 128001}).status_code == 422


@pytest.mark.parametrize("kind", [NotificationTypeV2.SUBSCRIBED, NotificationTypeV2.DID_RENEW])
def test_paid_notifications_require_current_apple_transaction(client, monkeypatch, kind):
    evidence = SimpleNamespace(transaction_id="123", account_token=uuid4())
    checked = []
    stored = []
    verifier = SimpleNamespace(
        verify_notification=lambda *a, **k: SimpleNamespace(notificationType=kind),
        verify_notification_purchase=lambda *a, **k: evidence,
        verify=lambda *a, **k: evidence)
    monkeypatch.setattr(billing, "configured_verifier", lambda: verifier)
    def current(identity):
        checked.append(identity)
        return SimpleNamespace(signedTransactionInfo="fresh-signed")
    monkeypatch.setattr(billing, "configured_status_client", lambda _: SimpleNamespace(get_transaction_info=current))
    monkeypatch.setattr(billing, "record_notification_purchase", lambda e, **k: stored.append(e))
    assert client.post("/v1/billing/apple/notifications", json={"signedPayload": "signed"}).status_code == 200
    assert checked == ["123"]
    assert stored == [evidence]


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(billing.router)
    principal = SimpleNamespace(user=SimpleNamespace(user_id=uuid4()))
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    @contextmanager
    def transaction(self, principal):
        yield object()
    monkeypatch.setattr(billing.FamilyRepository, "transaction", transaction)
    monkeypatch.setenv("DATABASE_URL", "test")
    return TestClient(app)


def test_account_returns_bound_token_without_caching(client, monkeypatch):
    token = uuid4()
    monkeypatch.setattr(billing.ApplePurchaseRegistry, "account_token", lambda db, uid: token)
    response = client.get("/v1/billing/account")
    assert response.status_code == 200
    assert response.json() == {"account_token": str(token)}
    assert response.headers["cache-control"] == "no-store"


def test_missing_configuration_fails_closed(client, monkeypatch):
    monkeypatch.delenv("STAY_APPLE_PRODUCTS", raising=False)
    response = client.post("/v1/billing/transactions", json={"signed_transaction": "untrusted"})
    assert response.status_code == 503


def test_evidence_storage_is_not_fulfillment(client, monkeypatch):
    token = uuid4()
    evidence = SimpleNamespace(transaction_id="123", product=SubscriptionProduct("plus", "monthly"))
    monkeypatch.setattr(billing.ApplePurchaseRegistry, "account_token", lambda db, uid: token)
    recorded = []
    monkeypatch.setattr(billing.ApplePurchaseRegistry, "record", lambda db, uid, value: recorded.append(value))
    monkeypatch.setattr(billing, "configured_verifier", lambda: SimpleNamespace(verify=lambda *a, **k: evidence))
    monkeypatch.setattr(billing, "configured_status_client", lambda verifier: object())
    monkeypatch.setattr(billing, "confirm_current_purchase", lambda client, verifier, value, **kw: value)
    monkeypatch.setattr(billing, "fulfill_subscription_purchase", lambda *a, **k: None)
    response = client.post("/v1/billing/transactions", json={"signed_transaction": "test-evidence"})
    assert response.status_code == 200
    assert response.json() == {"transaction_id": "123", "fulfilled": False}
    assert recorded == [evidence]


def test_client_cannot_supply_fulfillment_or_account(client):
    response = client.post("/v1/billing/transactions", json={
        "signed_transaction": "test", "fulfilled": True, "account_token": str(uuid4())})
    assert response.status_code == 422


def test_invalid_signature_never_reaches_registry(client, monkeypatch):
    monkeypatch.setattr(billing.ApplePurchaseRegistry, "account_token", lambda db, uid: uuid4())
    def reject(*args, **kwargs):
        raise billing.InvalidPurchaseEvidence()
    monkeypatch.setattr(billing, "configured_verifier", lambda: SimpleNamespace(verify=reject))
    response = client.post("/v1/billing/transactions", json={"signed_transaction": "invalid"})
    assert response.status_code == 422


@pytest.fixture
def current_purchase():
    now = datetime.now(timezone.utc)
    evidence = VerifiedSubscriptionEvidence("123", "100", uuid4(), "Sandbox", "stay.family.monthly",
        SubscriptionProduct("family", "monthly"), now, now)
    verifier = SimpleNamespace(environment=Environment.SANDBOX, bundle_id="app.stay", app_apple_id=123,
        verify=lambda signed, **kwargs: evidence)
    item = SimpleNamespace(originalTransactionId="100", status=Status.ACTIVE, signedTransactionInfo="signed")
    response = SimpleNamespace(environment=Environment.SANDBOX, bundleId="app.stay", appAppleId=123,
        data=[SimpleNamespace(lastTransactions=[item])])
    client = SimpleNamespace(get_all_subscription_statuses=lambda identity: response)
    return client, verifier, evidence, response, item, now


def test_current_paid_purchase_is_checked_with_apple(current_purchase):
    client, verifier, evidence, _, _, now = current_purchase
    assert billing.confirm_current_purchase(client, verifier, evidence, now=now) == evidence


@pytest.mark.parametrize("status", [Status.EXPIRED, Status.REVOKED, Status.BILLING_RETRY,
                                    Status.BILLING_GRACE_PERIOD, None])
def test_nonpaid_status_never_authorizes_new_credits(current_purchase, status):
    client, verifier, evidence, _, item, now = current_purchase
    item.status = status
    with pytest.raises(HTTPException) as error:
        billing.confirm_current_purchase(client, verifier, evidence, now=now)
    assert error.value.status_code == 409


@pytest.mark.parametrize("field,value", [("bundleId", "other.app"), ("appAppleId", 999),
                                       ("environment", Environment.PRODUCTION)])
def test_status_response_must_match_app_and_environment(current_purchase, field, value):
    client, verifier, evidence, response, _, now = current_purchase
    setattr(response, field, value)
    with pytest.raises(billing.InvalidPurchaseEvidence):
        billing.confirm_current_purchase(client, verifier, evidence, now=now)


def test_missing_and_ambiguous_subscription_status_fail_closed(current_purchase):
    client, verifier, evidence, response, item, now = current_purchase
    for items in ([], [item, item]):
        response.data[0].lastTransactions = items
        with pytest.raises(HTTPException) as error:
            billing.confirm_current_purchase(client, verifier, evidence, now=now)
        assert error.value.status_code == 409


def test_status_network_failure_is_retryable(current_purchase):
    client, verifier, evidence, _, _, now = current_purchase
    def timeout(identity):
        raise requests.Timeout("sensitive diagnostics")
    client.get_all_subscription_statuses = timeout
    with pytest.raises(HTTPException) as error:
        billing.confirm_current_purchase(client, verifier, evidence, now=now)
    assert error.value.status_code == 503
    assert "sensitive" not in error.value.detail


def test_missing_apple_key_is_not_a_success(current_purchase, monkeypatch):
    monkeypatch.delenv("STAY_APPLE_KEY_ID", raising=False)
    with pytest.raises(HTTPException) as error:
        billing.configured_status_client(current_purchase[1])
    assert error.value.status_code == 503


def test_replaced_transaction_cannot_be_credited(current_purchase):
    from dataclasses import replace
    client, verifier, evidence, _, _, now = current_purchase
    verifier.verify = lambda *args, **kwargs: replace(evidence, transaction_id="456")
    with pytest.raises(HTTPException) as error:
        billing.confirm_current_purchase(client, verifier, evidence, now=now)
    assert error.value.status_code == 409


def test_failed_status_check_never_records_purchase(client, monkeypatch):
    monkeypatch.setattr(billing.ApplePurchaseRegistry, "account_token", lambda db, uid: uuid4())
    monkeypatch.setattr(billing, "configured_verifier", lambda: SimpleNamespace(verify=lambda *a, **k: object()))
    monkeypatch.setattr(billing, "configured_status_client", lambda verifier: object())
    recorded = []
    monkeypatch.setattr(billing.ApplePurchaseRegistry, "record", lambda *args: recorded.append(args))
    def unavailable(*args, **kwargs):
        raise HTTPException(503, "Purchase confirmation is pending. Please try again.")
    monkeypatch.setattr(billing, "confirm_current_purchase", unavailable)
    response = client.post("/v1/billing/transactions", json={"signed_transaction": "signed"})
    assert response.status_code == 503
    assert recorded == []
