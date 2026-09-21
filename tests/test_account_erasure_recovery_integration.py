import asyncio
import os
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from app.services.account_erasure_repository import AccountErasureRepository
from app.services.account_erasure_service import AccountErasureService
from app.services.deletion_retention_service import DeletionRetentionService


@pytest.fixture
def db():
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated AP1 PostgreSQL')
    config = conninfo_to_dict(url)
    assert config.get('host', '').startswith('/private/tmp/STAY-AP1-PG-')
    assert config.get('dbname') == 'stay_ap1'
    return url


def test_begin_disables_account_and_survives_identity_removal(db):
    user = uuid4()
    repository = AccountErasureRepository(database_url=db)
    with psycopg.connect(db) as connection:
        connection.execute('INSERT INTO users(user_id) VALUES (%s)', (user,))
    repository.begin(user)
    with psycopg.connect(db) as connection:
        assert connection.execute('SELECT status FROM users WHERE user_id = %s', (user,)).fetchone()[0] == 'disabled'
        connection.execute('DELETE FROM users WHERE user_id = %s', (user,))
    repository.begin(user)
    assert repository.stage(user) == 'profiles'
    repository.advance(user, 'profiles', 'apple')
    repository.advance(user, 'apple', 'identity')
    repository.advance(user, 'identity', 'completed')


def test_recovery_after_identity_commit_does_not_repeat_apple_revocation(db):
    user = uuid4()
    with psycopg.connect(db) as connection:
        connection.execute("INSERT INTO account_erasure_requests(user_id, stage) VALUES (%s, 'identity')", (user,))
    identity = Mock()
    identity.get_user.return_value = None
    apple = AsyncMock()
    journal = AccountErasureRepository(database_url=db)
    service = AccountErasureService(identity_repository=identity, membership_repository=Mock(),
        profile_erasure_service=AsyncMock(), apple_verifier=apple, apple_cipher=Mock(), erasure_repository=journal)
    asyncio.run(service._resume(user))
    assert journal.stage(user) == 'completed'
    apple.revoke_refresh_token.assert_not_called()
    identity.delete_user_after_profile_erasure.assert_not_called()


def test_failed_apple_revocation_keeps_recoverable_phase(db):
    user = uuid4()
    with psycopg.connect(db) as connection:
        connection.execute("INSERT INTO account_erasure_requests(user_id, stage) VALUES (%s, 'apple')", (user,))
    identity = Mock()
    identity.get_apple_refresh_credential.return_value = b'encrypted'
    apple = AsyncMock()
    apple.revoke_refresh_token.side_effect = RuntimeError('unavailable')
    journal = AccountErasureRepository(database_url=db)
    service = AccountErasureService(identity_repository=identity, membership_repository=Mock(),
        profile_erasure_service=AsyncMock(), apple_verifier=apple, apple_cipher=Mock(), erasure_repository=journal)
    with pytest.raises(RuntimeError):
        asyncio.run(service._resume(user))
    assert journal.stage(user) == 'apple'
    assert user not in journal.pending()
    identity.delete_user_after_profile_erasure.assert_not_called()
    with psycopg.connect(db) as connection:
        connection.execute('DELETE FROM account_erasure_requests WHERE user_id = %s', (user,))


def test_runtime_retention_preserves_unfinished_and_recent_cleanup(db):
    identifiers = [str(uuid4()) for _ in range(3)]
    with psycopg.connect(db) as connection:
        for index, identifier in enumerate(identifiers):
            connection.execute("""INSERT INTO avatar_runtime_cleanup
                (session_id, profile_id, room_name, purpose_revision, purposes, expires_at, completed_at)
                VALUES (%s, %s, %s, 1, '{}', NOW(),
                    CASE %s WHEN 0 THEN NOW() - INTERVAL '31 days'
                            WHEN 1 THEN NOW() - INTERVAL '1 day' ELSE NULL END)""",
                (identifier, uuid4(), identifier, index))
    DeletionRetentionService(database_url=db).purge_completed_cleanup_records()
    with psycopg.connect(db) as connection:
        remaining = {row[0] for row in connection.execute('SELECT session_id FROM avatar_runtime_cleanup WHERE session_id = ANY(%s)', (identifiers,))}
        assert remaining == set(identifiers[1:])
        connection.execute('DELETE FROM avatar_runtime_cleanup WHERE session_id = ANY(%s)', (identifiers,))
