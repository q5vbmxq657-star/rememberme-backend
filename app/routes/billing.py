"""Authenticated purchase evidence intake. Fulfillment remains fail-closed."""
import json
import os
from uuid import UUID
from typing import Literal
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
import psycopg
import requests
from psycopg.rows import dict_row
from appstoreserverlibrary.models.NotificationTypeV2 import NotificationTypeV2
from appstoreserverlibrary.api_client import AppStoreServerAPIClient, APIException
from appstoreserverlibrary.models.Status import Status
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric import ec

from app.security.user_auth import require_authenticated_principal
from app.services.family_repository import FamilyRepository
from app.services.apple_purchase_registry import ApplePurchaseRegistry
from app.services.apple_credit_fulfillment import fulfill_subscription_purchase
from app.services.family_credit_ledger import FamilyCreditLedger, PersonalCreditAccount
from app.services.apple_purchase_verifier import (
    ApplePurchaseVerifier, InvalidPurchaseEvidence, SubscriptionProduct,
)

router = APIRouter(prefix="/v1/billing")


class VoiceCreditStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: UUID
    profile_id: UUID
    conversation_id: UUID
    funding: Literal["personal", "family", "automatic"]


@router.post("/voice-calls")
def start_voice_credit_call(request: VoiceCreditStart, principal=Depends(require_authenticated_principal)):
    from app.security.profile_authorization import require_profile_access
    from app.security.purpose_authorization import require_profile_purposes
    from app.services.call_credit_lifecycle import CallCreditLifecycle
    require_profile_access(principal=principal, profile_id=request.profile_id)
    require_profile_purposes(request.profile_id, {"memory_context"})
    result = CallCreditLifecycle(principal).start(**request.model_dump())
    return JSONResponse(jsonable_encoder(result),
                        headers={"Cache-Control": "no-store"})


@router.post("/voice-calls/{call_id}/renew")
def renew_voice_credit_call(call_id: UUID, principal=Depends(require_authenticated_principal)):
    from app.services.call_credit_lifecycle import CallCreditLifecycle
    return JSONResponse(jsonable_encoder(CallCreditLifecycle(principal).renew(call_id)),
                        headers={"Cache-Control": "no-store"})


@router.delete("/voice-calls/{call_id}")
def end_voice_credit_call(call_id: UUID, principal=Depends(require_authenticated_principal)):
    from app.services.call_credit_lifecycle import CallCreditLifecycle
    return JSONResponse(jsonable_encoder(CallCreditLifecycle(principal).end(call_id)),
                        headers={"Cache-Control": "no-store"})


def configured_status_client(verifier):
    try:
        key_id = os.environ.get("STAY_APPLE_KEY_ID", "").strip()
        issuer_id = os.environ.get("STAY_APPLE_ISSUER_ID", "").strip()
        key_path = os.environ.get("STAY_APPLE_PRIVATE_KEY_PATH", "").strip()
        key_pem = os.environ.get("STAY_APPLE_PRIVATE_KEY", "").strip()
        if not key_id or not issuer_id or bool(key_path) == bool(key_pem):
            raise ValueError("Missing server API configuration")
        signing_key = Path(key_path).read_bytes() if key_path else key_pem.encode("utf-8")
        parsed = load_pem_private_key(signing_key, password=None)
        if not isinstance(parsed, ec.EllipticCurvePrivateKey) or not isinstance(parsed.curve, ec.SECP256R1):
            raise ValueError("Invalid signing key")
        return AppStoreServerAPIClient(signing_key, key_id, issuer_id,
                                       verifier.bundle_id, verifier.environment)
    except (ValueError, TypeError, OSError):
        raise HTTPException(503, "Purchase confirmation is temporarily unavailable.") from None


def confirm_current_purchase(client, verifier, evidence, *, now):
    """Confirm current paid evidence, without treating it as credit fulfillment."""
    try:
        response = client.get_all_subscription_statuses(evidence.original_transaction_id)
    except (APIException, requests.RequestException):
        raise HTTPException(503, "Purchase confirmation is pending. Please try again.") from None
    if response is None:
        raise HTTPException(503, "Purchase confirmation is pending. Please try again.")
    if (response.environment != verifier.environment or response.bundleId != verifier.bundle_id
            or response.appAppleId != verifier.app_apple_id):
        raise InvalidPurchaseEvidence("Subscription status does not match this app")
    matches = [item for group in (response.data or []) for item in (group.lastTransactions or [])
               if item.originalTransactionId == evidence.original_transaction_id]
    if len(matches) != 1 or matches[0].status != Status.ACTIVE:
        # Grace-period access requires a separate signed renewal policy. It must
        # never be used as evidence of a newly paid credit allowance.
        raise HTTPException(409, "This purchase is not currently eligible for a new credit allowance.")
    current = verifier.verify(matches[0].signedTransactionInfo,
                              account_token=evidence.account_token, now=now)
    if current != evidence:
        raise HTTPException(409, "This subscription has changed. Restore purchases to refresh it.")
    return current


class PurchaseSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signed_transaction: str = Field(min_length=1, max_length=64000)


class AppleNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signedPayload: str = Field(min_length=1, max_length=128000)


def record_notification_revocation(evidence):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise HTTPException(503, "Purchase notification processing is unavailable.")
    with psycopg.connect(database_url, connect_timeout=10, row_factory=dict_row) as db:
        db.execute("SET LOCAL lock_timeout = '5s'")
        db.execute("SET LOCAL statement_timeout = '15s'")
        ApplePurchaseRegistry.record_revocation(db, evidence)


def record_notification_purchase(evidence, *, now, database_url=None):
    database_url = (database_url or os.environ.get("DATABASE_URL", "")).strip()
    if not database_url:
        raise HTTPException(503, "Purchase notification processing is unavailable.")
    with psycopg.connect(database_url, connect_timeout=10, row_factory=dict_row) as db:
        db.execute("SET LOCAL lock_timeout = '5s'")
        db.execute("SET LOCAL statement_timeout = '15s'")
        account = db.execute("""SELECT b.user_id FROM billing_accounts b JOIN users u USING(user_id)
            WHERE b.account_token=%s AND u.status='active' FOR KEY SHARE OF u""",
            (evidence.account_token,)).fetchone()
        if not account:
            raise HTTPException(409, "Purchase account is unavailable.")
        ApplePurchaseRegistry.record(db, account["user_id"], evidence)
        fulfill_subscription_purchase(db, account["user_id"], evidence, now=now)


def reconcile_paid_notification(verifier, signed_payload, *, now):
    evidence = verifier.verify_notification_purchase(signed_payload, now=now)
    try:
        current = configured_status_client(verifier).get_transaction_info(evidence.transaction_id)
    except (APIException, requests.RequestException):
        raise HTTPException(503, "Purchase confirmation is pending.") from None
    signed_transaction = getattr(current, "signedTransactionInfo", None)
    if not signed_transaction:
        raise HTTPException(503, "Purchase confirmation is pending.")
    confirmed = verifier.verify(signed_transaction, account_token=evidence.account_token,
                                now=now, require_active=False)
    if confirmed != evidence:
        raise HTTPException(409, "Purchase evidence has changed.")
    record_notification_purchase(confirmed, now=now)


@router.post("/apple/notifications")
def apple_notification(body: AppleNotification):
    # Apple authenticates with the signed envelope, not a user's access token.
    verifier = configured_verifier()
    now = datetime.now(timezone.utc)
    try:
        event = verifier.verify_notification(body.signedPayload, now=now)
        if event.notificationType == NotificationTypeV2.TEST:
            return JSONResponse({"received": True}, headers={"Cache-Control": "no-store"})
        if event.notificationType in {NotificationTypeV2.REFUND, NotificationTypeV2.REVOKE}:
            evidence = verifier.verify_revocation(body.signedPayload, now=now)
            record_notification_revocation(evidence)
            return JSONResponse({"received": True}, headers={"Cache-Control": "no-store"})
        if event.notificationType in {NotificationTypeV2.SUBSCRIBED, NotificationTypeV2.DID_RENEW}:
            reconcile_paid_notification(verifier, body.signedPayload, now=now)
            return JSONResponse({"received": True}, headers={"Cache-Control": "no-store"})
        # Do not discard renewal/reversal events by acknowledging unfinished work.
        raise HTTPException(503, "Subscription reconciliation is pending.")
    except InvalidPurchaseEvidence:
        raise HTTPException(422, "The purchase notification could not be verified.") from None
    except psycopg.Error:
        raise HTTPException(503, "Purchase notification processing is pending.") from None


def configured_verifier():
    try:
        products = json.loads(os.environ.get("STAY_APPLE_PRODUCTS", "{}"))
        roots = json.loads(os.environ.get("STAY_APPLE_ROOT_CERTIFICATES", "[]"))
        if not isinstance(products, dict) or not isinstance(roots, list):
            raise ValueError("Invalid configuration")
        return ApplePurchaseVerifier(
            root_certificates=[Path(path).read_bytes() for path in roots],
            bundle_id=os.environ.get("STAY_APPLE_BUNDLE_ID", ""),
            app_apple_id=int(os.environ.get("STAY_APPLE_APP_ID", "0")),
            environment=os.environ.get("STAY_APPLE_ENVIRONMENT", "Production"),
            products={key: SubscriptionProduct(**value) for key, value in products.items()},
        )
    except (ValueError, TypeError, OSError):
        raise HTTPException(503, "Purchases are not available yet.") from None


@router.get("/account")
def account(principal=Depends(require_authenticated_principal)):
    try:
        with FamilyRepository().transaction(principal) as db:
            token = ApplePurchaseRegistry.account_token(db, principal.user.user_id)
        return JSONResponse({"account_token": str(token)}, headers={"Cache-Control": "no-store"})
    except psycopg.Error:
        raise HTTPException(503, "Your purchase account could not be loaded. Please try again.") from None


@router.get("/credits")
def personal_credits(principal=Depends(require_authenticated_principal)):
    try:
        with FamilyRepository().transaction(principal) as db:
            token = ApplePurchaseRegistry.account_token(db, principal.user.user_id)
            credits = FamilyCreditLedger.balance(db, PersonalCreditAccount(token))
        return JSONResponse(credits, headers={"Cache-Control": "no-store"})
    except psycopg.Error:
        raise HTTPException(503, "Your credits could not be loaded. Please try again.") from None


@router.post("/transactions")
def submit(body: PurchaseSubmission, principal=Depends(require_authenticated_principal)):
    verifier = configured_verifier()
    try:
        repository = FamilyRepository()
        with repository.transaction(principal) as db:
            token = ApplePurchaseRegistry.account_token(db, principal.user.user_id)
        evidence = verifier.verify(body.signed_transaction, account_token=token,
                                   now=datetime.now(timezone.utc))
        evidence = confirm_current_purchase(configured_status_client(verifier), verifier, evidence,
                                            now=datetime.now(timezone.utc))
        with repository.transaction(principal) as db:
            ApplePurchaseRegistry.record(db, principal.user.user_id, evidence)
            fulfill_subscription_purchase(db, principal.user.user_id, evidence, now=datetime.now(timezone.utc))
        # Receipt storage is not delivery. The client must not finish StoreKit
        # transactions until entitlement reconciliation and credit fulfillment exist.
        return JSONResponse({"transaction_id": evidence.transaction_id, "fulfilled": False},
                            headers={"Cache-Control": "no-store"})
    except InvalidPurchaseEvidence:
        raise HTTPException(422, "The purchase could not be verified for this account.") from None
    except psycopg.Error:
        raise HTTPException(503, "Purchase confirmation is pending. Please try again.") from None
