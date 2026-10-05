"""Durable annual allowance catch-up; no grants without refreshed Apple evidence."""
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


class AppleAllowanceScheduler:
    def __init__(self, database_url, verifier, client):
        self.database_url = database_url
        self.verifier = verifier
        self.client = client

    def claim(self):
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as db:
            return db.execute("""WITH candidate AS (
                SELECT environment,transaction_id FROM apple_purchase_transactions
                WHERE environment=%s AND cadence='annual' AND revoked_at IS NULL
                  AND paid_from<=NOW() AND allowance_next_check_at<=NOW()
                ORDER BY allowance_next_check_at,transaction_id FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE apple_purchase_transactions t SET allowance_lease=%s,
                    allowance_next_check_at=NOW()+INTERVAL '5 minutes'
                FROM candidate c WHERE t.environment=c.environment AND t.transaction_id=c.transaction_id
                RETURNING t.*""", (self.verifier.environment.value,uuid4())).fetchone()

    def run_once(self):
        from app.routes.billing import record_notification_purchase
        from app.services.apple_purchase_verifier import InvalidPurchaseEvidence
        row = self.claim()
        if row is None:
            return False
        now = datetime.now(timezone.utc)
        next_check = now + timedelta(minutes=15)
        try:
            with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as db:
                binding = db.execute("""SELECT account_token FROM apple_subscription_ownership
                    WHERE environment=%s AND original_transaction_id=%s""",
                    (row['environment'],row['original_transaction_id'])).fetchone()
            if not binding:
                raise InvalidPurchaseEvidence('Subscription account binding is unavailable')
            current = self.client.get_transaction_info(row['transaction_id'])
            signed_transaction = getattr(current, 'signedTransactionInfo', None)
            if not signed_transaction:
                raise InvalidPurchaseEvidence('Apple transaction is unavailable')
            evidence = self.verifier.verify(signed_transaction,
                account_token=binding['account_token'], now=now, require_active=False)
            if (evidence.transaction_id != row['transaction_id']
                    or evidence.original_transaction_id != row['original_transaction_id']):
                raise InvalidPurchaseEvidence('Transaction identity changed')
            record_notification_purchase(evidence, now=now, database_url=self.database_url)
            next_check = None if evidence.paid_until <= now else now + timedelta(hours=1)
        finally:
            with psycopg.connect(self.database_url, connect_timeout=10) as db:
                db.execute("""UPDATE apple_purchase_transactions SET allowance_next_check_at=%s,
                    allowance_lease=NULL WHERE environment=%s AND transaction_id=%s AND allowance_lease=%s""",
                    (next_check,row['environment'],row['transaction_id'],row['allowance_lease']))
        return True


async def run_annual_allowances():
    from app.routes.billing import configured_verifier, configured_status_client
    while True:
        if os.environ.get('STAY_APPLE_ALLOWANCE_WORKER_ENABLED') == 'true':
            try:
                verifier = configured_verifier()
                scheduler = AppleAllowanceScheduler(os.environ['DATABASE_URL'], verifier,
                                                    configured_status_client(verifier))
                if await asyncio.to_thread(scheduler.run_once):
                    continue
            except Exception:
                logging.getLogger(__name__).warning('Annual allowance reconciliation pending; retry scheduled.')
        await asyncio.sleep(60)
