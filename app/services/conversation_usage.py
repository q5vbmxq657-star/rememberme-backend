"""Admission for voice turns requires an existing, bound credit reservation."""
from fastapi import HTTPException
from app.services.chat_usage import ChatUsage
from app.services.family_repository import FamilyRepository


class VoiceTurnUsage:
    def __init__(self, principal, request):
        self.principal = principal
        self.request = request

    def reserve(self):
        request = self.request
        with FamilyRepository().transaction(self.principal) as db:
            row = db.execute("""SELECT r.call_id FROM family_credit_reservations r
                WHERE r.call_id=%s AND r.member_id=%s AND r.auth_session_id=%s
                  AND r.profile_id=%s::uuid AND r.conversation_id=%s
                  AND r.mode='voice' AND r.settled_at IS NULL AND r.expires_at>NOW()
                  AND ((r.account_token IS NOT NULL AND EXISTS (
                    SELECT 1 FROM billing_accounts a WHERE a.account_token=r.account_token AND a.user_id=%s))
                    OR (r.family_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM family_members m WHERE m.family_id=r.family_id AND m.user_id=%s)))
                FOR UPDATE OF r""", (request.voice_call_id, self.principal.user.user_id,
                    self.principal.session_id, request.profile_id, request.conversation_id,
                    self.principal.user.user_id, self.principal.user.user_id)).fetchone()
            if not row:
                raise HTTPException(409, "This voice call is no longer active. Start a new call.")

    def finish(self, *, completed):
        # Voice is accounted by the call reservation, never by the chat allowance.
        return None


def conversation_usage(principal, request, *, chat_factory=ChatUsage):
    if request.channel == "voice":
        return VoiceTurnUsage(principal, request)
    return chat_factory(principal, request.request_id)
