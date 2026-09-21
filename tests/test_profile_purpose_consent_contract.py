import os
from uuid import uuid4
from unittest.mock import Mock

import psycopg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from psycopg.conninfo import conninfo_to_dict

from app.schemas.profile_consent import PurposeConsentUpdate
from app.security.purpose_authorization import require_profile_purposes
from app.services.profile_consent_repository import ProfileConsentRepository, ConsentRevisionConflict, ConsentAccessDenied
from app.services.pgvector_memory_service import PGVectorMemoryService
from app.schemas.vector_memory import SearchMemoryRequest


@pytest.fixture
def consent_database():
    url = os.getenv("STAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires the isolated local AP1 database")
    config = conninfo_to_dict(url)
    assert config.get("host", "").startswith("/private/tmp/STAY-AP1-PG-")
    assert config.get("dbname") == "stay_ap1"
    user, other_user, profile, other_profile, session = [uuid4() for _ in range(5)]
    with psycopg.connect(url) as connection:
        connection.execute("INSERT INTO users (user_id) VALUES (%s), (%s)", (user, other_user))
        connection.execute("INSERT INTO digital_human_profiles (profile_id, consent_verified) VALUES (%s, TRUE), (%s, TRUE)", (profile, other_profile))
        connection.execute("INSERT INTO profile_memberships (membership_id, user_id, profile_id, role) VALUES (%s,%s,%s,'owner')",
            (uuid4(), user, profile))
        connection.execute("""INSERT INTO user_sessions (session_id,user_id,refresh_token_hash,access_expires_at,refresh_expires_at)
            VALUES (%s,%s,%s,NOW()+INTERVAL '1 hour',NOW()+INTERVAL '1 day')""", (session,user,str(uuid4())))
    try:
        yield url, user, other_user, profile, other_profile, session
    finally:
        with psycopg.connect(url) as connection:
            connection.execute("DELETE FROM digital_human_profiles WHERE profile_id IN (%s,%s)", (profile,other_profile))
            connection.execute("DELETE FROM users WHERE user_id IN (%s,%s)", (user,other_user))


def update(revision, purposes):
    return PurposeConsentUpdate(expected_revision=revision, policy_version="avatar-consent-v1",
        purposes=purposes, understands_ai_disclosure=True, understands_revocation=True)


def test_legacy_boolean_does_not_grant_any_purpose(consent_database):
    url, user, _, profile, _, session = consent_database
    snapshot = ProfileConsentRepository(url).read(profile)
    assert snapshot.revision == 0 and snapshot.purposes == []
    with pytest.raises(HTTPException) as error:
        require_profile_purposes(profile, {"voice_synthesis"}, database_url=url)
    assert error.value.status_code == 403


def test_revocation_cannot_be_undone_by_a_stale_device(consent_database):
    url, user, _, profile, other, session = consent_database
    first, second = ProfileConsentRepository(url), ProfileConsentRepository(url)
    grant = first.update(profile_id=profile, user_id=user, session_id=session,
        update=update(0, ["voice_synthesis", "provider_processing"]))
    assert grant.revision == 1
    require_profile_purposes(profile, {"voice_synthesis"}, expected_revision=1, database_url=url)
    with pytest.raises(HTTPException):
        require_profile_purposes(profile, {"photo_likeness"}, database_url=url)
    revoked = second.update(profile_id=profile, user_id=user, session_id=session, update=update(1, []))
    assert revoked.revision == 2 and revoked.purposes == []
    with pytest.raises(ConsentRevisionConflict):
        first.update(profile_id=profile, user_id=user, session_id=session,
            update=update(1, ["voice_synthesis", "provider_processing"]))
    assert first.read(profile) == revoked
    assert first.read(other).purposes == []
    with psycopg.connect(url) as connection:
        events = connection.execute("SELECT revision,purposes FROM profile_purpose_consent_events WHERE profile_id=%s ORDER BY revision", (profile,)).fetchall()
    assert events == [(1, ["provider_processing", "voice_synthesis"]), (2, [])]


@pytest.mark.parametrize("change", ["foreign_user", "foreign_profile", "foreign_session", "logout", "inactive", "erasure"])
def test_grants_require_current_profile_owner_and_session(consent_database, change):
    url, user, other_user, profile, other, session = consent_database
    with psycopg.connect(url) as connection:
        if change == "logout":
            connection.execute("UPDATE user_sessions SET revoked_at=NOW() WHERE session_id=%s", (session,))
        elif change == "inactive":
            connection.execute("UPDATE profile_memberships SET status='revoked' WHERE profile_id=%s", (profile,))
        elif change == "erasure":
            connection.execute("INSERT INTO digital_human_profile_erasure_requests (request_id,profile_id,idempotency_key) VALUES (%s,%s,%s)",
                (uuid4(), profile, str(uuid4())))
    with pytest.raises(ConsentAccessDenied):
        ProfileConsentRepository(url).update(profile_id=other if change == "foreign_profile" else profile,
            user_id=other_user if change == "foreign_user" else user,
            session_id=uuid4() if change == "foreign_session" else session,
            update=update(0, ["memory_context", "provider_processing"]))
    assert ProfileConsentRepository(url).read(profile).revision == 0


def test_search_never_sends_query_to_provider_without_permission(consent_database):
    url, _, _, profile, _, _ = consent_database
    instance = PGVectorMemoryService(database_url=url, client=object())
    instance._embed = Mock(side_effect=AssertionError("Private query reached the provider"))
    with pytest.raises(HTTPException):
        instance.search(SearchMemoryRequest(profile_id=str(profile), query="private text"))
    instance._embed.assert_not_called()


@pytest.mark.parametrize("purposes,disclosure,revocation", [
    (["voice_synthesis"], True, True),
    (["voice_synthesis", "provider_processing"], False, True),
    (["voice_synthesis", "provider_processing"], True, False),
    (["provider_processing", "provider_processing"], True, True),
    (["unknown"], True, True),
])
def test_unacknowledged_or_invalid_grant_is_rejected(purposes, disclosure, revocation):
    with pytest.raises(ValidationError):
        PurposeConsentUpdate(expected_revision=0, policy_version="avatar-consent-v1", purposes=purposes,
            understands_ai_disclosure=disclosure, understands_revocation=revocation)
