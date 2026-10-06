import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
import psycopg
import pytest
from fastapi import HTTPException
from tests.test_plan_access_postgres import principal
from app.services.family_repository import FamilyRepository
from app.services.apple_purchase_registry import ApplePurchaseRegistry
from app.services.family_credit_ledger import FamilyCreditLedger as Ledger, PersonalCreditAccount
from app.services.call_credit_lifecycle import CallCreditLifecycle
from app.services.conversation_usage import VoiceTurnUsage


@pytest.fixture
def call(principal):
    principal.access_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    principal.user.is_active = True
    repo = FamilyRepository()
    profile = uuid4()
    with repo.transaction(principal) as db:
        db.execute("INSERT INTO digital_human_profiles(profile_id) VALUES (%s)", (profile,))
        db.execute("""INSERT INTO profile_memberships(membership_id,user_id,profile_id,role)
            VALUES (%s,%s,%s,'owner')""", (uuid4(),principal.user.user_id,profile))
        db.execute("""INSERT INTO profile_purpose_consents(profile_id,revision,policy_version,purposes)
            VALUES (%s,1,'avatar-consent-v1',ARRAY['memory_context','provider_processing'])""", (profile,))
        owner = PersonalCreditAccount(ApplePurchaseRegistry.account_token(db, principal.user.user_id))
        Ledger.grant(db, owner, evidence_key=str(uuid4()), units=180)
    return CallCreditLifecycle(principal), dict(call_id=uuid4(), profile_id=profile,
        conversation_id=uuid4(), funding="personal"), owner


def test_concurrent_start_retry_creates_one_reservation(call, principal):
    service, args, owner = call
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.start(**args), range(8)))
    assert len({r["call_id"] for r in results}) == 1
    with FamilyRepository().transaction(principal) as db:
        assert Ledger.balance(db, owner)["reserved_units"] == 60


def test_bound_voice_does_not_touch_chat_usage_and_end_revokes_it(call, principal):
    service, args, owner = call
    service.start(**args)
    request = SimpleNamespace(voice_call_id=args["call_id"], profile_id=str(args["profile_id"]),
                              conversation_id=args["conversation_id"])
    VoiceTurnUsage(principal, request).reserve()
    service.end(args["call_id"])
    service.end(args["call_id"])
    with pytest.raises(HTTPException) as error:
        VoiceTurnUsage(principal, request).reserve()
    assert error.value.status_code == 409
    with FamilyRepository().transaction(principal) as db:
        assert Ledger.balance(db, owner)["reserved_units"] == 0
        assert db.execute("SELECT count(*) AS n FROM chat_weekly_usage WHERE user_id=%s",
                          (principal.user.user_id,)).fetchone()["n"] == 0
        assert db.execute("SELECT count(*) AS n FROM family_credit_entries WHERE evidence_key=%s",
                          ("call:"+str(args["call_id"]),)).fetchone()["n"] <= 1


def test_expired_call_cannot_send_or_restart(call, principal):
    service, args, _ = call
    service.start(**args)
    with psycopg.connect(os.environ["STAY_TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE family_credit_reservations SET expires_at=NOW()-INTERVAL '1 second' WHERE call_id=%s",
                   (args["call_id"],))
    with pytest.raises(HTTPException):
        service.start(**args)
    with pytest.raises(HTTPException):
        VoiceTurnUsage(principal, SimpleNamespace(voice_call_id=args["call_id"],
            profile_id=str(args["profile_id"]), conversation_id=args["conversation_id"])).reserve()


def test_cross_conversation_reservation_reuse_is_rejected(call, principal):
    service, args, _ = call
    service.start(**args)
    with pytest.raises(HTTPException):
        service.start(**{**args, "conversation_id": uuid4()})
    with pytest.raises(HTTPException):
        VoiceTurnUsage(principal, SimpleNamespace(voice_call_id=args["call_id"],
            profile_id=str(args["profile_id"]), conversation_id=uuid4())).reserve()


def test_expired_reservation_is_recovered_exactly_once(call, principal):
    service, args, owner = call
    service.start(**args)
    with psycopg.connect(os.environ["STAY_TEST_DATABASE_URL"]) as db:
        db.execute("""UPDATE family_credit_reservations SET created_at=NOW()-INTERVAL '70 seconds',
            expires_at=NOW()-INTERVAL '10 seconds' WHERE call_id=%s""", (args["call_id"],))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: CallCreditLifecycle.sweep_expired(), range(2)))
    with FamilyRepository().transaction(principal) as db:
        balance = Ledger.balance(db, owner)
        assert balance["reserved_units"] == 0
        assert balance["balance_units"] == 120


def test_renewal_extends_same_reservation_and_rechecks_consent(call, principal):
    service, args, owner = call
    first = service.start(**args)
    with psycopg.connect(os.environ["STAY_TEST_DATABASE_URL"]) as db:
        db.execute("""UPDATE family_credit_reservations SET created_at=created_at-INTERVAL '20 seconds',
            expires_at=expires_at-INTERVAL '20 seconds' WHERE call_id=%s""", (args["call_id"],))
    renewed = service.renew(args["call_id"])
    assert renewed["call_id"] == first["call_id"]
    with FamilyRepository().transaction(principal) as db:
        assert 80 <= Ledger.balance(db, owner)["reserved_units"] <= 82
        db.execute("UPDATE profile_purpose_consents SET purposes=ARRAY[]::text[],revision=revision+1 WHERE profile_id=%s",
                   (args["profile_id"],))
    with pytest.raises(HTTPException):
        service.renew(args["call_id"])
    service.end(args["call_id"])


def test_depleted_credit_returns_confirmed_limit_on_renewal(call, principal):
    service, args, owner = call
    service.start(**args)
    with FamilyRepository().transaction(principal) as db:
        # Reserve the rest on another active call, without mutating the grant.
        Ledger.reserve(db, owner, principal.user.user_id, uuid4(), mode="voice", seconds=120)
        db.execute("""UPDATE family_credit_reservations SET created_at=created_at-INTERVAL '40 seconds',
            expires_at=expires_at-INTERVAL '40 seconds' WHERE call_id=%s""", (args["call_id"],))
    with pytest.raises(HTTPException) as error:
        service.renew(args["call_id"])
    assert error.value.status_code == 402
    assert error.value.detail["reason"] == "insufficient_credits"
    service.end(args["call_id"])


def test_session_erasure_removes_reservation_without_blocking_deletion(call, principal):
    service, args, _ = call
    service.start(**args)
    with psycopg.connect(os.environ["STAY_TEST_DATABASE_URL"]) as db:
        db.execute("DELETE FROM user_sessions WHERE session_id=%s", (principal.session_id,))
        assert db.execute("SELECT count(*) FROM family_credit_reservations WHERE call_id=%s",
                          (args["call_id"],)).fetchone()[0] == 0
