"""One account-scoped generic-voice allowance, using the canonical ledger."""
from app.services.family_credit_ledger import FamilyCreditLedger, PersonalCreditAccount
from app.services.apple_purchase_registry import ApplePurchaseRegistry
from app.services.plan_access import effective_plan

FREE_VOICE_SECONDS = 300


def grant_free_voice_trial(db, user_id):
    if effective_plan(db, user_id) != "free":
        return
    token = ApplePurchaseRegistry.account_token(db, user_id)
    FamilyCreditLedger.grant(
        db, PersonalCreditAccount(token),
        evidence_key=f"free-voice-trial:v1:{user_id}",
        units=FREE_VOICE_SECONDS,
    )


def voice_version_for_account(principal, requested_version):
    from app.services.family_repository import FamilyRepository
    with FamilyRepository().transaction(principal) as db:
        return "generic" if effective_plan(db, principal.user.user_id) == "free" else requested_version
