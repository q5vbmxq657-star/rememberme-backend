"""Opt-in integration tests against an isolated, fully migrated PostgreSQL DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest
from fastapi import HTTPException
from app.services.chat_usage import ChatUsage
from app.services.profile_membership_repository import ProfileMembershipRepository
from app.schemas.vector_memory import IndexMemoryRequest, VectorMemoryItem, MemorySyncMetadata
from app.services.pgvector_memory_service import PGVectorMemoryService


@pytest.fixture
def principal(monkeypatch):
    url = os.environ.get("STAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("An isolated STAY_TEST_DATABASE_URL is required")
    config = conninfo_to_dict(url)
    assert config.get("host", "").startswith("/private/tmp/stay-quota-db.")
    assert config.get("dbname") == "postgres"
    monkeypatch.setenv("DATABASE_URL", url)
    uid, sid = uuid4(), uuid4()
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO users(user_id) VALUES (%s)", (uid,))
        db.execute("""INSERT INTO user_sessions(session_id,user_id,refresh_token_hash,
            access_expires_at,refresh_expires_at) VALUES (%s,%s,%s,NOW()+INTERVAL '1 hour',
            NOW()+INTERVAL '1 day')""", (sid, uid, str(uuid4())))
    yield SimpleNamespace(user=SimpleNamespace(user_id=uid), session_id=sid)
    # Test records remain in this disposable instance for failure diagnosis.


def test_twenty_parallel_requests_admit_exactly_ten(principal):
    def submit(_):
        try:
            ChatUsage(principal).reserve()
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(submit, range(20)))
    assert results.count(200) == 10
    assert results.count(402) == 10


def test_parallel_replay_reserves_only_once(principal):
    request_id = uuid4()
    def submit(_):
        try:
            ChatUsage(principal, request_id).reserve()
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert results.count(200) == 1
    assert results.count(409) == 7


def test_failure_releases_slot_but_success_does_not(principal):
    requests = [ChatUsage(principal) for _ in range(10)]
    for request in requests:
        request.reserve()
    requests[0].finish(completed=True)
    requests[0].finish(completed=False)
    with pytest.raises(HTTPException) as error:
        ChatUsage(principal).reserve()
    assert error.value.status_code == 402
    requests[1].finish(completed=False)
    ChatUsage(principal).reserve()


def test_upgrade_and_revocation_are_observed_without_cached_entitlements(principal):
    url = os.environ["STAY_TEST_DATABASE_URL"]
    for _ in range(10):
        usage = ChatUsage(principal)
        usage.reserve()
        usage.finish(completed=True)
    with pytest.raises(HTTPException) as error:
        ChatUsage(principal).reserve()
    assert error.value.status_code == 402
    token, transaction = uuid4(), str(uuid4().int)
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO billing_accounts(account_token,user_id) VALUES (%s,%s)",
                   (token,principal.user.user_id))
        db.execute("""INSERT INTO apple_subscription_ownership
            (environment,original_transaction_id,account_token) VALUES ('Production',%s,%s)""",
                   (transaction,token))
        db.execute("""INSERT INTO apple_purchase_transactions
            (environment,transaction_id,original_transaction_id,product_id,plan,cadence,paid_from,paid_until)
            VALUES ('Production',%s,%s,'test.plus','plus','monthly',NOW()-INTERVAL '1 day',NOW()+INTERVAL '29 days')""",
                   (transaction,transaction))
    usage = ChatUsage(principal)
    usage.reserve()
    usage.finish(completed=True)
    with psycopg.connect(url) as db:
        db.execute("UPDATE apple_purchase_transactions SET revoked_at=NOW() WHERE transaction_id=%s",
                   (transaction,))
    with pytest.raises(HTTPException) as error:
        ChatUsage(principal).reserve()
    assert error.value.status_code == 402


def test_parallel_profile_creation_preserves_free_limit(principal):
    def create(_):
        try:
            ProfileMembershipRepository().provision_owned_profile(
                user_id=principal.user.user_id, profile_id=uuid4(), consent_verified=True)
            return 201
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))
    assert results.count(201) == 1
    assert results.count(402) == 7


def test_denied_journey_write_preserves_published_memories(principal):
    url = os.environ["STAY_TEST_DATABASE_URL"]
    profile = str(uuid4())
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO digital_human_profiles(profile_id) VALUES (%s)", (profile,))
        db.execute("""INSERT INTO profile_purpose_consents
            (profile_id,revision,policy_version,purposes) VALUES
            (%s,1,'avatar-consent-v1',ARRAY['memory_context','provider_processing'])""", (profile,))
    service = PGVectorMemoryService(client=object(), database_url=url)
    service._embed = lambda _: [1.0] + [0.0] * 1535
    def request(prompt, revision):
        return IndexMemoryRequest(profile_id=profile, expected_revision=revision, memories=[
            VectorMemoryItem(id="story", profile_id=profile, title="Story", summary="Evidence",
                type="text", sync_metadata=MemorySyncMetadata(guided_prompt_id=prompt))])
    service.index(request("love.partnership", 0), user_id=principal.user.user_id)
    before = service.content_snapshot(profile)
    def denied(_):
        try:
            service.index(request("school-days.teacher", before["revision"]),
                          user_id=principal.user.user_id)
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(denied, range(8))) == [402] * 8
    assert service.content_snapshot(profile) == before
