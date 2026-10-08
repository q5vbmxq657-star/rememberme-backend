"""Personal and Family allowances in the existing purchase/credit transaction."""
from fastapi import HTTPException

from app.services.family_credit_ledger import FamilyCreditLedger, PersonalCreditAccount
from app.services.subscription_credit_period import monthly_credit_periods
from app.services.pricing_catalog import PLUS_MONTHLY_CREDITS


def fulfill_subscription_purchase(db, user_id, evidence, *, now):
    receipt = db.execute("""SELECT revoked_at FROM apple_purchase_transactions
        WHERE environment=%s AND transaction_id=%s FOR UPDATE""",
        (evidence.environment, evidence.transaction_id)).fetchone()
    if not receipt or receipt["revoked_at"] is not None:
        raise HTTPException(409, "This purchase is not eligible for credits.")
    account = db.execute("SELECT user_id FROM billing_accounts WHERE account_token=%s", (evidence.account_token,)).fetchone()
    if not account or account["user_id"] != user_id:
        raise HTTPException(403, "This purchase belongs to another account.")
    if evidence.product.plan == "plus":
        owner = PersonalCreditAccount(evidence.account_token)
        units = PLUS_MONTHLY_CREDITS * FamilyCreditLedger.UNITS_PER_CREDIT
    else:
        owner = bind_family(db, user_id, evidence)
        units = FamilyCreditLedger.MONTHLY_UNITS
    periods = monthly_credit_periods(original_transaction_id=evidence.original_transaction_id,
        environment=evidence.environment, paid_from=evidence.paid_from, paid_until=evidence.paid_until,
        now=now, cadence=evidence.product.cadence)
    for period in periods:
        FamilyCreditLedger.grant(db, owner, evidence_key=period.evidence_key,
            units=units, environment=evidence.environment)
        entry = db.execute("SELECT entry_id FROM family_credit_entries WHERE evidence_key=%s",
                           (period.evidence_key,)).fetchone()
        db.execute("""INSERT INTO apple_family_credit_allocations(environment,transaction_id,entry_id)
            VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
            (evidence.environment,evidence.transaction_id,entry["entry_id"]))
        allocation = db.execute("""SELECT environment,transaction_id FROM apple_family_credit_allocations
            WHERE entry_id=%s""", (entry["entry_id"],)).fetchone()
        if (allocation["environment"],allocation["transaction_id"]) != (evidence.environment,evidence.transaction_id):
            raise HTTPException(409, "This billing period has conflicting purchase evidence.")
    return FamilyCreditLedger.balance(db, owner, environment=evidence.environment)


def bind_family(db, user_id, evidence):
    group = db.execute("""SELECT family_id FROM family_groups
        WHERE organizer_id=%s FOR UPDATE""", (user_id,)).fetchone()
    if not group:
        raise HTTPException(409, "Create your family before restoring this purchase.")
    family_id = group["family_id"]
    db.execute("""INSERT INTO apple_family_credit_bindings
        (environment,original_transaction_id,family_id) VALUES (%s,%s,%s)
        ON CONFLICT DO NOTHING""", (evidence.environment,evidence.original_transaction_id,family_id))
    binding = db.execute("""SELECT family_id FROM apple_family_credit_bindings
        WHERE environment=%s AND original_transaction_id=%s FOR UPDATE""",
        (evidence.environment,evidence.original_transaction_id)).fetchone()
    if binding["family_id"] != family_id:
        raise HTTPException(409, "This subscription belongs to another family.")
    return family_id


def reverse_subscription_purchase(db, *, environment, transaction_id):
    # The caller holds the same purchase lock as fulfillment, including refund-first delivery.
    grants = db.execute("""SELECT e.family_id,e.account_token,e.entry_id FROM apple_family_credit_allocations a
        JOIN family_credit_entries e USING(entry_id)
        WHERE a.environment=%s AND a.transaction_id=%s ORDER BY e.family_id,e.entry_id""",
        (environment,transaction_id)).fetchall()
    for grant in grants:
        owner = grant["family_id"] if grant["family_id"] else PersonalCreditAccount(grant["account_token"])
        FamilyCreditLedger.refund_grant(db, owner, grant["entry_id"])
