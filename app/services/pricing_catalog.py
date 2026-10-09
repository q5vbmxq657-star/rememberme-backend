"""Approved plan terms, not purchase evidence or an entitlement grant."""
from app.services.apple_store_configuration import configured_products

UNITS_PER_CREDIT = 60
VOICE_UNITS_PER_SECOND = 1
VIDEO_UNITS_PER_SECOND = 10
FAMILY_MONTHLY_CREDITS = 300
PLUS_MONTHLY_CREDITS = 50


def store_products():
    try:
        return [{"id": identity, "plan": product.plan, "cadence": product.cadence}
                for identity, product in configured_products().items()]
    except (TypeError, ValueError):
        return []


def pricing_catalog():
    return {
        "version": 2,
        "purchases_available": False,
        "store_products": store_products(),
        "video_available": False,
        "credits_roll_over": True,
        "voice_credits_per_minute": 1,
        "video_credits_per_minute": 10,
        "plans": [
            {"id": "free", "name": "Free", "monthly_credits": 0,
             "memory_level_limit": 5, "weekly_chat_messages": 10,
             "avatar_limit": 1, "family_member_limit": None},
            {"id": "plus", "name": "Plus", "monthly_credits": PLUS_MONTHLY_CREDITS,
             "memory_level_limit": None, "weekly_chat_messages": None,
             "avatar_limit": 1, "family_member_limit": None},
            {"id": "family", "name": "Family", "monthly_credits": FAMILY_MONTHLY_CREDITS,
             "memory_level_limit": None, "weekly_chat_messages": None,
             "avatar_limit": None, "family_member_limit": 6},
        ],
    }
