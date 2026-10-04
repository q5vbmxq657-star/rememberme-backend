"""Durable ownership and replay protection; no automatic entitlement grants."""
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from app.services.apple_purchase_verifier import VerifiedSubscriptionEvidence, VerifiedPurchaseRevocation


class ApplePurchaseRegistry:
    @classmethod
    def record_revocation(cls, db, evidence: VerifiedPurchaseRevocation):
        """Persist verified notification and revocation in the same transaction."""
        inserted = db.execute("""INSERT INTO apple_revocation_notifications
            (environment,notification_id,transaction_id,revoked_at) VALUES (%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING notification_id""",
            (evidence.environment,evidence.notification_id,evidence.transaction_id,evidence.revoked_at)).fetchone()
        if not inserted:
            prior = db.execute("""SELECT transaction_id,revoked_at FROM apple_revocation_notifications
                WHERE environment=%s AND notification_id=%s""",
                (evidence.environment,evidence.notification_id)).fetchone()
            if prior["transaction_id"] != evidence.transaction_id or prior["revoked_at"] != evidence.revoked_at:
                raise HTTPException(409, "Conflicting purchase notification.")
            return
        cls.revoke(db, environment=evidence.environment, transaction_id=evidence.transaction_id,
                   revoked_at=evidence.revoked_at)

    @staticmethod
    def account_token(db, user_id: UUID) -> UUID:
        # Serializes account creation with account deletion and concurrent requests.
        if not db.execute("SELECT user_id FROM users WHERE user_id=%s FOR UPDATE", (user_id,)).fetchone():
            raise HTTPException(404, "Account not found.")
        row = db.execute("SELECT account_token FROM billing_accounts WHERE user_id=%s", (user_id,)).fetchone()
        if row:
            return row["account_token"]
        token = uuid4()
        db.execute("INSERT INTO billing_accounts(account_token,user_id) VALUES (%s,%s)", (token,user_id))
        return token

    @staticmethod
    def _transaction_lock(db, environment, transaction_id):
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                   (f"apple-purchase:{environment}:{transaction_id}",))

    @classmethod
    def record(cls, db, user_id: UUID, evidence: VerifiedSubscriptionEvidence):
        token = cls.account_token(db, user_id)
        if token != evidence.account_token:
            raise HTTPException(403, "This purchase belongs to another account.")
        cls._transaction_lock(db, evidence.environment, evidence.transaction_id)
        db.execute("""INSERT INTO apple_subscription_ownership
            (environment,original_transaction_id,account_token) VALUES (%s,%s,%s)
            ON CONFLICT DO NOTHING""",
            (evidence.environment,evidence.original_transaction_id,token))
        owner = db.execute("""SELECT account_token FROM apple_subscription_ownership
            WHERE environment=%s AND original_transaction_id=%s FOR UPDATE""",
            (evidence.environment,evidence.original_transaction_id)).fetchone()
        if owner["account_token"] != token:
            raise HTTPException(409, "This subscription is already linked to another account.")
        db.execute("""INSERT INTO apple_purchase_transactions
            (environment,transaction_id,original_transaction_id,product_id,plan,cadence,paid_from,paid_until,revoked_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
              (SELECT revoked_at FROM apple_purchase_revocations WHERE environment=%s AND transaction_id=%s))
            ON CONFLICT DO NOTHING""",
            (evidence.environment,evidence.transaction_id,evidence.original_transaction_id,evidence.product_id,
             evidence.product.plan,evidence.product.cadence,evidence.paid_from,evidence.paid_until,
             evidence.environment,evidence.transaction_id))
        row = db.execute("""SELECT * FROM apple_purchase_transactions
            WHERE environment=%s AND transaction_id=%s FOR UPDATE""",
            (evidence.environment,evidence.transaction_id)).fetchone()
        expected = dict(original_transaction_id=evidence.original_transaction_id,
            product_id=evidence.product_id,plan=evidence.product.plan,cadence=evidence.product.cadence,
            paid_from=evidence.paid_from,paid_until=evidence.paid_until)
        if any(row[key] != value for key,value in expected.items()):
            raise HTTPException(409, "The purchase evidence conflicts with the recorded transaction.")
        return row

    @classmethod
    def revoke(cls, db, *, environment: str, transaction_id: str, revoked_at: datetime):
        """Only invoke after validating the signed Apple revocation notification."""
        if (environment not in {"Production","Sandbox"} or not isinstance(transaction_id,str)
                or not transaction_id.isascii() or not transaction_id.isdecimal()
                or len(transaction_id) > 128 or not isinstance(revoked_at,datetime)
                or revoked_at.tzinfo is None or revoked_at.utcoffset() is None):
            raise ValueError("Verified revocation identity and timestamp are required")
        cls._transaction_lock(db, environment, transaction_id)
        db.execute("""INSERT INTO apple_purchase_revocations(environment,transaction_id,revoked_at)
            VALUES (%s,%s,%s) ON CONFLICT(environment,transaction_id)
            DO UPDATE SET revoked_at=LEAST(apple_purchase_revocations.revoked_at,EXCLUDED.revoked_at)""",
            (environment,transaction_id,revoked_at))
        db.execute("""UPDATE apple_purchase_transactions SET revoked_at=
            (SELECT revoked_at FROM apple_purchase_revocations WHERE environment=%s AND transaction_id=%s)
            WHERE environment=%s AND transaction_id=%s""", (environment,transaction_id,environment,transaction_id))
