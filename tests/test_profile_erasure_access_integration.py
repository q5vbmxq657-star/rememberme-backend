from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from app.services.digital_human_profile_repository import DigitalHumanProfileRepository
from app.services.profile_erasure_access import ProfileErasureAccess, ErasureAccessDenied


@pytest.mark.parametrize('legacy', [False, True])
def test_owner_retry_survives_erasure_and_graph_deletion_but_not_session_revocation(legacy):
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated AP1 PostgreSQL')
    config = conninfo_to_dict(url)
    assert config.get('host', '').startswith('/private/tmp/STAY-AP1-PG-')
    assert config.get('dbname') == 'stay_ap1'
    user, foreign, profile, session, foreign_session = [uuid4() for _ in range(5)]
    principal = SimpleNamespace(user=SimpleNamespace(user_id=user), session_id=session,
        access_expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    other = SimpleNamespace(user=SimpleNamespace(user_id=foreign), session_id=foreign_session,
        access_expires_at=principal.access_expires_at)
    repository = DigitalHumanProfileRepository(database_url=url)
    access = ProfileErasureAccess(repository)
    with psycopg.connect(url) as connection:
        for identity, session_id in [(user, session), (foreign, foreign_session)]:
            connection.execute('INSERT INTO users(user_id) VALUES (%s)', (identity,))
            connection.execute("""INSERT INTO user_sessions(session_id, user_id, refresh_token_hash,
                access_expires_at, refresh_expires_at) VALUES (%s, %s, %s,
                NOW() + INTERVAL '1 hour', NOW() + INTERVAL '1 day')""", (session_id, identity, str(uuid4())))
        connection.execute('INSERT INTO digital_human_profiles(profile_id) VALUES (%s)', (profile,))
        connection.execute("""INSERT INTO profile_memberships(membership_id, user_id, profile_id, role, status)
            VALUES (%s, %s, %s, 'owner', 'active')""", (uuid4(), user, profile))
    try:
        if legacy:
            repository.create_profile_erasure_request(request_id=uuid4(), profile_id=profile,
                                                      idempotency_key=f'legacy:{uuid4()}')
        with pytest.raises(ErasureAccessDenied):
            access.authorize_request(other, profile)
        request = access.authorize_request(principal, profile)
        assert access.authorize_request(principal, profile)['request_id'] == request['request_id']
        repository.delete_profile_graph(profile_id=profile)
        assert access.authorize_request(principal, profile)['request_id'] == request['request_id']
        with pytest.raises(ErasureAccessDenied):
            access.authorize_request(other, profile)
        with psycopg.connect(url) as connection:
            connection.execute('UPDATE user_sessions SET revoked_at = NOW() WHERE session_id = %s', (session,))
        with pytest.raises(ErasureAccessDenied):
            access.authorize_request(principal, profile)
    finally:
        with psycopg.connect(url) as connection:
            connection.execute('DELETE FROM digital_human_profile_erasure_requests WHERE request_id = %s', (request['request_id'],))
            connection.execute('DELETE FROM digital_human_profiles WHERE profile_id = %s', (profile,))
            connection.execute('DELETE FROM users WHERE user_id = ANY(%s)', ([user, foreign],))
