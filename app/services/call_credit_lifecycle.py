"""Short, server-timed voice leases backed by the existing credit ledger."""
import math
import os
import psycopg
from psycopg.rows import dict_row
from datetime import timedelta
from fastapi import HTTPException
from app.services.family_repository import FamilyRepository
from app.services.family_credit_ledger import FamilyCreditLedger as Ledger, PersonalCreditAccount
from app.services.apple_purchase_registry import ApplePurchaseRegistry
from app.services.plan_access import access_decision, effective_plan


class CallCreditLifecycle:
    WINDOW_SECONDS = 60

    @classmethod
    def sweep_expired(cls):
        url = os.environ["DATABASE_URL"]
        with psycopg.connect(url, connect_timeout=10, row_factory=dict_row) as db:
            candidates = db.execute("""SELECT * FROM family_credit_reservations
                WHERE expires_at<=NOW() AND settled_at IS NULL ORDER BY expires_at LIMIT 100""").fetchall()
        for candidate in candidates:
            with psycopg.connect(url, connect_timeout=10, row_factory=dict_row) as db:
                db.execute("SET LOCAL lock_timeout='5s'")
                db.execute("SET LOCAL statement_timeout='15s'")
                Ledger.lock(db, cls.owner(candidate))
                row = db.execute("""SELECT * FROM family_credit_reservations WHERE call_id=%s
                    AND settled_at IS NULL AND expires_at<=NOW() FOR UPDATE""", (candidate["call_id"],)).fetchone()
                if row:
                    cls.settle(db, row, row["expires_at"])

    def __init__(self, principal):
        self.principal = principal
        self.repository = FamilyRepository()

    @staticmethod
    def owner(row):
        return PersonalCreditAccount(row["account_token"]) if row["account_token"] else row["family_id"]

    @staticmethod
    def settle(db, row, now):
        elapsed = max(0, math.ceil((min(now, row["expires_at"]) - row["created_at"]).total_seconds()))
        return Ledger.settle(db, CallCreditLifecycle.owner(row), row["call_id"],
                             verified_seconds=min(elapsed, row["reserved_units"]))

    def start(self, *, call_id, profile_id, conversation_id, funding):
        uid = self.principal.user.user_id
        with self.repository.transaction(self.principal) as db:
            if funding == "automatic":
                membership = db.execute("SELECT family_id FROM family_members WHERE user_id=%s", (uid,)).fetchone()
                funding = "family" if membership and effective_plan(db, uid) == "family" else "personal"
            if funding == "family":
                owner = self.repository.group(db, uid)["family_id"]
            elif funding == "personal":
                owner = PersonalCreditAccount(ApplePurchaseRegistry.account_token(db, uid))
                from app.services.free_voice_trial import grant_free_voice_trial
                grant_free_voice_trial(db, uid)
            else:
                raise HTTPException(422, "Choose a credit account.")
            Ledger.lock(db, owner)
            prior = db.execute("SELECT * FROM family_credit_reservations WHERE call_id=%s", (call_id,)).fetchone()
            now = db.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if prior:
                if (prior["member_id"], prior["auth_session_id"], prior["profile_id"], prior["conversation_id"], self.owner(prior)) != (
                    uid, self.principal.session_id, profile_id, conversation_id, owner):
                    raise HTTPException(409, "This call identifier is already in use.")
                if prior["settled_at"] is not None or prior["expires_at"] <= now:
                    raise HTTPException(409, "This call has ended. Start a new call.")
                return self.response(prior)
            # Reclaim abandoned leases under the same owner lock as admission.
            from app.services.family_credit_ledger import credit_owner
            fid, token = credit_owner(owner)
            expired = db.execute("""SELECT * FROM family_credit_reservations
                WHERE family_id IS NOT DISTINCT FROM %s AND account_token IS NOT DISTINCT FROM %s
                  AND settled_at IS NULL AND expires_at<=%s FOR UPDATE""", (fid,token,now)).fetchall()
            for row in expired:
                self.settle(db, row, now)
            available = Ledger.balance(db, owner)["available_units"]
            decision = access_decision(plan=effective_plan(db, uid), action="voice_call", available_units=available)
            if not decision["allowed"]:
                raise HTTPException(402, detail={**decision, "message": "Add credits to start a voice call."})
            seconds = min(self.WINDOW_SECONDS, available)
            Ledger.reserve(db, owner, uid, call_id, mode="voice", seconds=seconds)
            row = db.execute("""UPDATE family_credit_reservations SET profile_id=%s,auth_session_id=%s,
                conversation_id=%s,created_at=%s,expires_at=%s WHERE call_id=%s RETURNING *""",
                (profile_id,self.principal.session_id,conversation_id,now,now+timedelta(seconds=seconds),call_id)).fetchone()
            return self.response(row)

    def end(self, call_id):
        with self.repository.transaction(self.principal) as db:
            row = db.execute("""SELECT * FROM family_credit_reservations
                WHERE call_id=%s AND member_id=%s AND auth_session_id=%s AND expires_at IS NOT NULL""",
                (call_id,self.principal.user.user_id,self.principal.session_id)).fetchone()
            if not row:
                raise HTTPException(404, "Call not found.")
            owner = self.owner(row)
            Ledger.lock(db, owner)
            row = db.execute("SELECT * FROM family_credit_reservations WHERE call_id=%s FOR UPDATE", (call_id,)).fetchone()
            if row["settled_at"] is None:
                now = db.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
                self.settle(db, row, now)
            return {"call_id": call_id, "state": "ended"}

    def renew(self, call_id):
        with self.repository.transaction(self.principal) as db:
            uid = self.principal.user.user_id
            row = db.execute("""SELECT * FROM family_credit_reservations WHERE call_id=%s
                AND member_id=%s AND auth_session_id=%s AND expires_at IS NOT NULL""",
                (call_id,uid,self.principal.session_id)).fetchone()
            if not row:
                raise HTTPException(404, "Call not found.")
            from app.security.profile_authorization import require_profile_access
            from app.security.purpose_authorization import require_profile_purposes
            require_profile_access(principal=self.principal, profile_id=row["profile_id"])
            require_profile_purposes(row["profile_id"], {"memory_context"})
            owner = self.owner(row)
            Ledger.lock(db, owner)
            row = db.execute("SELECT * FROM family_credit_reservations WHERE call_id=%s FOR UPDATE", (call_id,)).fetchone()
            now = db.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if row["settled_at"] is not None or row["expires_at"] <= now:
                raise HTTPException(409, "This call has ended. Start a new call.")
            if row["family_id"] and not db.execute("SELECT 1 FROM family_members WHERE family_id=%s AND user_id=%s",
                                                    (row["family_id"],uid)).fetchone():
                raise HTTPException(403, "Family membership is required.")
            elapsed = max(0, math.ceil((now-row["created_at"]).total_seconds()))
            desired = elapsed + self.WINDOW_SECONDS
            additional = min(max(0, desired-row["reserved_units"]), Ledger.balance(db, owner)["available_units"])
            if additional:
                row = db.execute("""UPDATE family_credit_reservations
                    SET reserved_units=reserved_units+%s,expires_at=created_at+(reserved_units+%s)*INTERVAL '1 second'
                    WHERE call_id=%s RETURNING *""", (additional,additional,call_id)).fetchone()
            elif (row["expires_at"]-now).total_seconds() <= 25:
                decision = access_decision(plan=effective_plan(db, uid), action="voice_call", available_units=0)
                raise HTTPException(402, detail={**decision, "message": "Add credits to continue your voice call."})
            return self.response(row)

    @staticmethod
    def response(row):
        return {"call_id": row["call_id"], "conversation_id": row["conversation_id"],
                "profile_id": row["profile_id"], "expires_at": row["expires_at"], "state": "active"}
