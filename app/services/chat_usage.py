"""Account-wide admission: pending requests count against the weekly allowance."""
from uuid import uuid4
from fastapi import HTTPException
from app.services.family_repository import FamilyRepository
from app.services.plan_access import access_decision, effective_plan


class ChatUsage:
    def __init__(self, principal, request_id=None):
        self.principal = principal
        self.request_id = request_id or uuid4()
        self.repository = FamilyRepository()

    def reserve(self):
        with self.repository.transaction(self.principal) as db:
            uid = self.principal.user.user_id
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                       ("chat-usage:" + str(uid),))
            if db.execute("SELECT 1 FROM chat_weekly_usage WHERE user_id=%s AND request_id=%s",
                          (uid, self.request_id)).fetchone():
                raise HTTPException(409, "This message has already been submitted.")
            week = db.execute("SELECT date_trunc('week', NOW() AT TIME ZONE 'UTC')::date AS start").fetchone()["start"]
            used = db.execute("SELECT COUNT(*) AS used FROM chat_weekly_usage WHERE user_id=%s AND week_start=%s",
                              (uid, week)).fetchone()["used"]
            decision = access_decision(plan=effective_plan(db, uid), action="chat_message", used=used)
            if not decision["allowed"]:
                raise HTTPException(402, detail={**decision,
                    "message": "You've used your 10 weekly messages. Explore plans to keep chatting."})
            db.execute("INSERT INTO chat_weekly_usage(user_id,request_id,week_start) VALUES (%s,%s,%s)",
                       (uid, self.request_id, week))

    def finish(self, *, completed):
        with self.repository.transaction(self.principal) as db:
            if completed:
                db.execute("UPDATE chat_weekly_usage SET completed=TRUE WHERE user_id=%s AND request_id=%s",
                           (self.principal.user.user_id, self.request_id))
            else:
                db.execute("DELETE FROM chat_weekly_usage WHERE user_id=%s AND request_id=%s AND NOT completed",
                           (self.principal.user.user_id, self.request_id))
