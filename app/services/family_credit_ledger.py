"""Integer accounting inside the existing Family transaction.

One credit is 60 units: a voice second costs one unit, a video second ten.
Only trusted server-side payment/usage verification may invoke mutations.
No client-facing grant or settlement endpoint exists.
"""
from uuid import UUID, uuid4
from dataclasses import dataclass

from fastapi import HTTPException
from app.services.pricing_catalog import (
    UNITS_PER_CREDIT, FAMILY_MONTHLY_CREDITS,
    VOICE_UNITS_PER_SECOND, VIDEO_UNITS_PER_SECOND,
)


@dataclass(frozen=True)
class PersonalCreditAccount:
    account_token: UUID

    def __post_init__(self):
        if not isinstance(self.account_token, UUID):
            raise ValueError("A bound billing account is required")


def credit_owner(owner):
    return (None, owner.account_token) if isinstance(owner, PersonalCreditAccount) else (owner, None)


class FamilyCreditLedger:
    UNITS_PER_CREDIT = UNITS_PER_CREDIT
    MONTHLY_UNITS = FAMILY_MONTHLY_CREDITS * UNITS_PER_CREDIT

    @staticmethod
    def lock(db, family_id):
        if isinstance(family_id, PersonalCreditAccount):
            if not db.execute("SELECT account_token FROM billing_accounts WHERE account_token=%s FOR UPDATE",
                              (family_id.account_token,)).fetchone():
                raise HTTPException(404, "Billing account not found.")
            return
        if not db.execute("SELECT family_id FROM family_groups WHERE family_id=%s FOR UPDATE", (family_id,)).fetchone():
            raise HTTPException(404, "Family not found.")

    @staticmethod
    def balance(db, family_id, *, environment="Production"):
        if environment not in {"Production", "Sandbox"}:
            raise ValueError("Unsupported credit environment")
        fid, token = credit_owner(family_id)
        totals = db.execute("""SELECT
            COALESCE((SELECT SUM(units) FROM family_credit_entries
                WHERE family_id IS NOT DISTINCT FROM %s AND account_token IS NOT DISTINCT FROM %s
                AND environment=%s),0) AS balance_units,
            COALESCE((SELECT SUM(reserved_units) FROM family_credit_reservations
                WHERE family_id=%s AND settled_at IS NULL AND %s='Production'),0) AS reserved_units""",
            (fid,token,environment,fid,environment)).fetchone()
        balance, reserved = int(totals["balance_units"]), int(totals["reserved_units"])
        return {"balance_units": balance, "reserved_units": reserved,
                "available_units": max(0, balance-reserved), "units_per_credit": UNITS_PER_CREDIT,
                "monthly_credits_roll_over": True}

    @classmethod
    def grant(cls, db, family_id, *, evidence_key, units, environment="Production"):
        if environment not in {"Production", "Sandbox"}:
            raise ValueError("Unsupported credit environment")
        if type(units) is not int or not 0 < units <= 9223372036854775807:
            raise ValueError("A grant requires positive integer units.")
        if not isinstance(evidence_key, str) or not evidence_key.strip() or len(evidence_key) > 200 or evidence_key.startswith("call:"):
            raise ValueError("A grant requires a unique verified payment or period reference.")
        cls.lock(db, family_id)
        fid, token = credit_owner(family_id)
        inserted = db.execute("""INSERT INTO family_credit_entries(entry_id,family_id,account_token,evidence_key,units,kind,environment)
            VALUES (%s,%s,%s,%s,%s,'grant',%s) ON CONFLICT(evidence_key) DO NOTHING RETURNING entry_id""",
            (uuid4(),fid,token,evidence_key,units,environment)).fetchone()
        if not inserted:
            prior = db.execute("SELECT family_id,account_token,units,kind,environment FROM family_credit_entries WHERE evidence_key=%s", (evidence_key,)).fetchone()
            if (prior["family_id"], prior["account_token"]) != (fid, token) or prior["units"] != units or prior["kind"] != "grant" or prior["environment"] != environment:
                raise HTTPException(409, "The credit receipt has already been used differently.")
        balance = cls.balance(db, family_id, environment=environment)
        if balance["balance_units"] > 9223372036854775807:
            raise HTTPException(409, "The credit balance exceeds the supported limit.")
        return balance

    @classmethod
    def refund_grant(cls, db, family_id, entry_id):
        """Reverse a proven grant once; retain debt when credits were already used."""
        cls.lock(db, family_id)
        fid, token = credit_owner(family_id)
        grant = db.execute("""SELECT units,environment FROM family_credit_entries
            WHERE entry_id=%s AND family_id IS NOT DISTINCT FROM %s
                AND account_token IS NOT DISTINCT FROM %s AND kind='grant' FOR UPDATE""",
            (entry_id, fid, token)).fetchone()
        if not grant:
            raise HTTPException(409, "The original credit grant could not be confirmed.")
        db.execute("""INSERT INTO family_credit_entries
            (entry_id,family_id,account_token,evidence_key,units,kind,refund_of,environment)
            VALUES (%s,%s,%s,%s,%s,'refund',%s,%s) ON CONFLICT(refund_of) DO NOTHING""",
            (uuid4(), fid, token, "refund:" + str(entry_id), -grant["units"], entry_id, grant["environment"]))
        return cls.balance(db, family_id, environment=grant["environment"])

    @classmethod
    def reserve(cls, db, family_id, member_id, call_id, *, mode, seconds):
        if mode not in ("voice", "video") or type(seconds) is not int or not 0 < seconds <= 86400:
            raise ValueError("A reservation requires a supported mode and bounded whole seconds.")
        units = seconds * (VIDEO_UNITS_PER_SECOND if mode == "video" else VOICE_UNITS_PER_SECOND)
        cls.lock(db, family_id)
        if not db.execute("SELECT 1 FROM family_members WHERE family_id=%s AND user_id=%s", (family_id,member_id)).fetchone():
            raise HTTPException(403, "Family membership is required.")
        prior = db.execute("SELECT * FROM family_credit_reservations WHERE call_id=%s", (call_id,)).fetchone()
        if prior:
            if (prior["family_id"],prior["member_id"],prior["mode"],prior["reserved_units"]) != (family_id,member_id,mode,units) or prior["settled_at"] is not None:
                raise HTTPException(409, "This call reservation has already changed.")
            return cls.balance(db, family_id)
        if cls.balance(db, family_id)["available_units"] < units:
            raise HTTPException(409, "Your family does not have enough available credits.")
        db.execute("""INSERT INTO family_credit_reservations(call_id,family_id,member_id,mode,reserved_units)
            VALUES (%s,%s,%s,%s,%s)""", (call_id,family_id,member_id,mode,units))
        return cls.balance(db, family_id)

    @classmethod
    def settle(cls, db, family_id, call_id, *, verified_seconds):
        if type(verified_seconds) is not int or verified_seconds < 0:
            raise ValueError("Settlement requires verified nonnegative whole seconds.")
        cls.lock(db, family_id)
        row = db.execute("SELECT * FROM family_credit_reservations WHERE family_id=%s AND call_id=%s FOR UPDATE", (family_id,call_id)).fetchone()
        if not row:
            raise HTTPException(404, "Call reservation not found.")
        units = verified_seconds * (VIDEO_UNITS_PER_SECOND if row["mode"] == "video" else VOICE_UNITS_PER_SECOND)
        if units > row["reserved_units"]:
            raise HTTPException(409, "Usage exceeds the authorized reservation.")
        if row["settled_at"] is not None:
            if row["consumed_units"] != units:
                raise HTTPException(409, "This call was settled with different usage.")
            return cls.balance(db, family_id)
        if units:
            db.execute("""INSERT INTO family_credit_entries(entry_id,family_id,evidence_key,units,kind)
                VALUES (%s,%s,%s,%s,'usage')""", (uuid4(),family_id,"call:"+str(call_id),-units))
        db.execute("UPDATE family_credit_reservations SET consumed_units=%s,settled_at=NOW() WHERE call_id=%s", (units,call_id))
        return cls.balance(db, family_id)
