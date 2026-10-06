"""Shared access decisions. Callers supply authoritative, transactional usage."""
from app.services.pricing_catalog import pricing_catalog


def access_decision(*, plan, action, used=0, requested_level=None,
                    available_units=None):
    plans = {item["id"]: item for item in pricing_catalog()["plans"]}
    if plan not in plans:
        raise ValueError("Unknown plan")
    if type(used) is not int or used < 0:
        raise ValueError("Invalid usage")
    terms = plans[plan]
    if action == "read":
        reason = None
    elif action == "create_avatar":
        limit = terms["avatar_limit"]
        reason = "avatar_limit" if limit is not None and used >= limit else None
    elif action == "chat_message":
        limit = terms["weekly_chat_messages"]
        reason = "weekly_chat_limit" if limit is not None and used >= limit else None
    elif action == "journey_level":
        if type(requested_level) is not int or requested_level < 1:
            raise ValueError("A positive journey level is required")
        limit = terms["memory_level_limit"]
        reason = "journey_limit" if limit is not None and requested_level > limit else None
    elif action == "voice_call":
        if type(available_units) is not int or available_units < 0:
            raise ValueError("Confirmed available credit is required")
        reason = "insufficient_credits" if available_units == 0 else None
    else:
        raise ValueError("Unknown action")
    return {"version": 1, "action": action, "allowed": reason is None,
            "reason": reason, "plan": plan}


def effective_plan(db, user_id):
    """Resolve current production purchases; never infer rights from product IDs."""
    rows = db.execute("""SELECT DISTINCT t.plan FROM apple_purchase_transactions t
        JOIN apple_subscription_ownership o USING(environment, original_transaction_id)
        JOIN billing_accounts a ON a.account_token=o.account_token
        WHERE t.environment='Production' AND t.revoked_at IS NULL
          AND t.paid_from<=NOW() AND t.paid_until>NOW()
          AND (a.user_id=%s OR (t.plan='family' AND EXISTS (
            SELECT 1 FROM apple_family_credit_bindings b
            JOIN family_members m ON m.family_id=b.family_id
            WHERE b.environment=t.environment
              AND b.original_transaction_id=t.original_transaction_id AND m.user_id=%s)))
        """, (user_id, user_id)).fetchall()
    found = {row["plan"] for row in rows}
    return "family" if "family" in found else "plus" if "plus" in found else "free"
