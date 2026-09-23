"""Integer accounting inside the existing Family transaction.

One credit is 60 units: a voice second costs one unit, a video second ten.
Only trusted server-side payment/usage verification may invoke mutations.
No client-facing grant or settlement endpoint exists.
"""
from uuid import uuid4

from fastapi import HTTPException


class FamilyCreditLedger:
    UNITS_PER_CREDIT = 60
    MONTHLY_UNITS = 300 * UNITS_PER_CREDIT

    @staticmethod
    def lock(db, family_id):
        if not db.execute("SELECT family_id FROM family_groups WHERE family_id=%s FOR UPDATE", (family_id,)).fetchone():
            raise HTTPException(404, "Family not found.")

    @staticmethod
    def balance(db, family_id):
        totals = db.execute("""SELECT
            COALESCE((SELECT SUM(units) FROM family_credit_entries WHERE family_id=%s),0) AS balance_units,
            COALESCE((SELECT SUM(reserved_units) FROM family_credit_reservations
                WHERE family_id=%s AND settled_at IS NULL),0) AS reserved_units""", (family_id,family_id)).fetchone()
        balance, reserved = int(totals["balance_units"]), int(totals["reserved_units"])
        return {"balance_units": balance, "reserved_units": reserved,
                "available_units": balance-reserved, "units_per_credit": 60,
                "monthly_credits_roll_over": True}

    @classmethod
    def grant(cls, db, family_id, *, evidence_key, units):
        if type(units) is not int or not 0 < units <= 9223372036854775807:
            raise ValueError("A grant requires positive integer units.")
        if not isinstance(evidence_key, str) or not evidence_key.strip() or len(evidence_key) > 200 or evidence_key.startswith("call:"):
            raise ValueError("A grant requires a unique verified payment or period reference.")
        cls.lock(db, family_id)
        inserted = db.execute("""INSERT INTO family_credit_entries(entry_id,family_id,evidence_key,units,kind)
            VALUES (%s,%s,%s,%s,'grant') ON CONFLICT(evidence_key) DO NOTHING RETURNING entry_id""",
            (uuid4(),family_id,evidence_key,units)).fetchone()
        if not inserted:
            prior = db.execute("SELECT family_id,units,kind FROM family_credit_entries WHERE evidence_key=%s", (evidence_key,)).fetchone()
            if prior["family_id"] != family_id or prior["units"] != units or prior["kind"] != "grant":
                raise HTTPException(409, "The credit receipt has already been used differently.")
        balance = cls.balance(db, family_id)
        if balance["balance_units"] > 9223372036854775807:
            raise HTTPException(409, "The credit balance exceeds the supported limit.")
        return balance

    @classmethod
    def reserve(cls, db, family_id, member_id, call_id, *, mode, seconds):
        if mode not in ("voice", "video") or type(seconds) is not int or not 0 < seconds <= 86400:
            raise ValueError("A reservation requires a supported mode and bounded whole seconds.")
        units = seconds * (10 if mode == "video" else 1)
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
        units = verified_seconds * (10 if row["mode"] == "video" else 1)
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
