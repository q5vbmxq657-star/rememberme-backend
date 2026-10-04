"""Validate purchase evidence using Apple's certificate/signature verifier.

This is not an entitlement grant: current subscription status, refunds and
durable transaction ownership must be reconciled before fulfillment.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.models.InAppOwnershipType import InAppOwnershipType
from appstoreserverlibrary.models.Type import Type
from appstoreserverlibrary.models.NotificationTypeV2 import NotificationTypeV2
from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier, VerificationException


class InvalidPurchaseEvidence(ValueError):
    pass


@dataclass(frozen=True)
class SubscriptionProduct:
    plan: str
    cadence: str

    def __post_init__(self):
        if self.plan not in {"plus", "family"} or self.cadence not in {"monthly", "annual"}:
            raise ValueError("Unsupported subscription product configuration")


@dataclass(frozen=True)
class VerifiedSubscriptionEvidence:
    transaction_id: str
    original_transaction_id: str
    account_token: UUID
    environment: str
    product_id: str
    product: SubscriptionProduct
    paid_from: datetime
    paid_until: datetime


@dataclass(frozen=True)
class VerifiedPurchaseRevocation:
    notification_id: UUID
    environment: str
    transaction_id: str
    revoked_at: datetime


class ApplePurchaseVerifier:
    def __init__(self, *, root_certificates: list[bytes], bundle_id: str,
                 app_apple_id: int, environment: str,
                 products: dict[str, SubscriptionProduct]):
        if (environment not in {"Production", "Sandbox"} or not bundle_id.strip()
                or type(app_apple_id) is not int or app_apple_id <= 0
                or not root_certificates or any(not root for root in root_certificates)
                or not products or any(not key.strip() for key in products)
                or any(not isinstance(value, SubscriptionProduct) for value in products.values())):
            raise ValueError("Verified App Store configuration is required")
        self.environment = Environment(environment)
        self.bundle_id = bundle_id
        self.app_apple_id = app_apple_id
        self.products = dict(products)
        self.verifier = SignedDataVerifier(root_certificates, True, self.environment,
                                           bundle_id, app_apple_id)

    def verify_revocation(self, signed_notification: str, *, now: datetime) -> VerifiedPurchaseRevocation:
        """Verify both the outer notification and nested transaction signature."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("A timezone-aware clock is required")
        if (not isinstance(signed_notification, str) or not signed_notification.isascii()
                or not 1 <= len(signed_notification) <= 128000):
            raise InvalidPurchaseEvidence("Invalid notification evidence")
        try:
            notification = self.verifier.verify_and_decode_notification(signed_notification)
            if (notification.version != "2.0" or notification.notificationType not in
                    {NotificationTypeV2.REFUND, NotificationTypeV2.REVOKE}):
                raise InvalidPurchaseEvidence("This event requires subscription reconciliation")
            data = notification.data
            if (data is None or data.bundleId != self.bundle_id or data.environment != self.environment
                    or data.appAppleId != self.app_apple_id):
                raise InvalidPurchaseEvidence("Notification does not match this app")
            transaction = self.verifier.verify_and_decode_signed_transaction(data.signedTransactionInfo)
            if (transaction.bundleId != self.bundle_id or transaction.environment != self.environment
                    or transaction.productId not in self.products
                    or transaction.type != Type.AUTO_RENEWABLE_SUBSCRIPTION):
                raise InvalidPurchaseEvidence("Notification transaction does not match this app")
            identity = transaction.transactionId
            if (not isinstance(identity, str) or not identity.isascii() or not identity.isdecimal()
                    or len(identity) > 128):
                raise InvalidPurchaseEvidence("Invalid notification transaction identity")
            dates = (transaction.revocationDate, transaction.signedDate, notification.signedDate)
            if any(type(value) is not int or value <= 0 for value in dates):
                raise InvalidPurchaseEvidence("Invalid revocation dates")
            current_ms = int(now.timestamp() * 1000)
            if not transaction.revocationDate <= transaction.signedDate <= notification.signedDate <= current_ms:
                raise InvalidPurchaseEvidence("Invalid revocation chronology")
            return VerifiedPurchaseRevocation(UUID(notification.notificationUUID), self.environment.value,
                identity, datetime.fromtimestamp(transaction.revocationDate / 1000, timezone.utc))
        except InvalidPurchaseEvidence:
            raise
        except (VerificationException, ValueError, TypeError, AttributeError, OverflowError, OSError):
            raise InvalidPurchaseEvidence("Notification evidence could not be verified") from None

    def verify(self, signed_transaction: str, *, account_token: UUID,
               now: datetime) -> VerifiedSubscriptionEvidence:
        if not isinstance(account_token, UUID) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("A bound account and timezone-aware clock are required")
        if (not isinstance(signed_transaction, str) or not signed_transaction.isascii()
                or not 1 <= len(signed_transaction) <= 64000):
            raise InvalidPurchaseEvidence("Invalid purchase evidence")
        try:
            payload = self.verifier.verify_and_decode_signed_transaction(signed_transaction)
            product = self.products.get(payload.productId)
            if (payload.bundleId != self.bundle_id or payload.environment != self.environment
                    or product is None or payload.type != Type.AUTO_RENEWABLE_SUBSCRIPTION
                    or payload.inAppOwnershipType != InAppOwnershipType.PURCHASED
                    or payload.revocationDate is not None or payload.isUpgraded is True
                    or UUID(payload.appAccountToken) != account_token):
                raise InvalidPurchaseEvidence("Purchase does not match this account or product")
            identifiers = (payload.transactionId, payload.originalTransactionId)
            if any(not isinstance(value, str) or not value.isascii() or not value.isdecimal()
                   or len(value) > 128 for value in identifiers):
                raise InvalidPurchaseEvidence("Invalid purchase identity")
            dates = (payload.purchaseDate, payload.expiresDate, payload.signedDate)
            if any(type(value) is not int or value <= 0 for value in dates):
                raise InvalidPurchaseEvidence("Invalid purchase dates")
            current_ms = int(now.timestamp() * 1000)
            if not payload.purchaseDate <= payload.signedDate <= current_ms:
                raise InvalidPurchaseEvidence("Invalid purchase chronology")
            if not payload.purchaseDate <= current_ms < payload.expiresDate:
                raise InvalidPurchaseEvidence("Purchase is not active")
            return VerifiedSubscriptionEvidence(
                transaction_id=payload.transactionId, original_transaction_id=payload.originalTransactionId,
                account_token=account_token, environment=self.environment.value,
                product_id=payload.productId, product=product,
                paid_from=datetime.fromtimestamp(payload.purchaseDate / 1000, timezone.utc),
                paid_until=datetime.fromtimestamp(payload.expiresDate / 1000, timezone.utc))
        except InvalidPurchaseEvidence:
            raise
        except (VerificationException, ValueError, TypeError, AttributeError, OverflowError, OSError):
            raise InvalidPurchaseEvidence("Purchase evidence could not be verified") from None
