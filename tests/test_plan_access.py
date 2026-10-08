import pytest

from app.services.plan_access import access_decision, effective_plan


@pytest.mark.parametrize("action,kwargs,allowed", [
    ("read", {}, True),
    ("create_avatar", {"used": 0}, True),
    ("create_avatar", {"used": 1}, False),
    ("chat_message", {"used": 9}, True),
    ("chat_message", {"used": 10}, False),
    ("journey_level", {"requested_level": 5}, True),
    ("journey_level", {"requested_level": 6}, False),
    ("voice_call", {"available_units": 0}, False),
    ("voice_call", {"available_units": 1}, True),
])
def test_free_boundaries(action, kwargs, allowed):
    assert access_decision(plan="free", action=action, **kwargs)["allowed"] is allowed


@pytest.mark.parametrize("plan", ["plus", "family"])
def test_paid_limits_do_not_imply_free_voice(plan):
    assert access_decision(plan=plan, action="chat_message", used=100)["allowed"]
    assert access_decision(plan=plan, action="create_avatar", used=2)["allowed"] is (plan == "family")
    assert access_decision(plan=plan, action="journey_level", requested_level=20)["allowed"]
    assert not access_decision(plan=plan, action="voice_call", available_units=0)["allowed"]


def test_plus_includes_exactly_one_avatar_without_blocking_reads():
    assert access_decision(plan="plus", action="create_avatar", used=0)["allowed"]
    assert access_decision(plan="plus", action="create_avatar", used=1)["reason"] == "avatar_limit"
    assert access_decision(plan="plus", action="read", used=2)["allowed"]


@pytest.mark.parametrize("kwargs", [
    {"action": "unknown"}, {"action": "chat_message", "used": -1},
    {"action": "journey_level"}, {"action": "journey_level", "requested_level": True},
    {"action": "voice_call"}, {"action": "voice_call", "available_units": -1},
])
def test_unknown_evidence_is_not_an_upgrade_denial(kwargs):
    with pytest.raises(ValueError):
        access_decision(plan="free", **kwargs)


def test_effective_plan_requires_current_production_evidence():
    class DB:
        def execute(self, sql, params):
            assert "t.environment='Production'" in sql
            assert "t.revoked_at IS NULL" in sql
            assert "t.paid_until>NOW()" in sql
            assert "t.paid_from<=NOW()" in sql
            assert "JOIN family_members" in sql
            assert params == ("user", "user")
            return self

        def fetchall(self):
            return [{"plan": "plus"}, {"plan": "family"}]

    assert effective_plan(DB(), "user") == "family"
