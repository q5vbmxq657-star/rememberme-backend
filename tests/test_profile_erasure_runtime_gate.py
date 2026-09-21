import asyncio
import os
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from app.services.digital_human_profile_repository import DigitalHumanProfileRepository
from app.services.profile_erasure_service import ProfileErasureService, ProfileErasureServiceError


@pytest.mark.parametrize('stage', ['requested', 'database_cleanup'])
def test_pending_runtime_blocks_erasure_until_worker_completion(stage):
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated AP1 PostgreSQL')
    config = conninfo_to_dict(url)
    assert config.get('host', '').startswith('/private/tmp/STAY-AP1-PG-')
    assert config.get('dbname') == 'stay_ap1'
    profile, session = uuid4(), str(uuid4())
    repository = DigitalHumanProfileRepository(database_url=url)
    with psycopg.connect(url) as connection:
        connection.execute('INSERT INTO digital_human_profiles(profile_id) VALUES (%s)', (profile,))
        connection.execute("""INSERT INTO avatar_runtime_cleanup
            (session_id, profile_id, room_name, purpose_revision, purposes, expires_at)
            VALUES (%s, %s, %s, 1, '{}', NOW() + INTERVAL '1 hour')""", (session, profile, session))
    request = repository.create_profile_erasure_request(request_id=uuid4(), profile_id=profile,
                                                        idempotency_key=str(uuid4()))
    if stage == 'database_cleanup':
        with psycopg.connect(url) as connection:
            connection.execute("UPDATE digital_human_profile_erasure_requests SET status = 'database_cleanup' WHERE request_id = %s", (request['request_id'],))
    provider, voice, storage = AsyncMock(), AsyncMock(), Mock()
    storage.delete_profile_assets.return_value = []
    service = ProfileErasureService(repository=repository, avatar_provider=provider,
                                   voice_service=voice, media_storage=storage)
    try:
        with pytest.raises(ProfileErasureServiceError):
            asyncio.run(service._run(request))
        current = repository.get_profile_erasure_request(request_id=request['request_id'])
        assert current['status'] == 'retryable_failed'
        assert current['resume_stage'] == ('provider_cleanup' if stage == 'requested' else stage)
        assert repository.get(profile) is not None
        provider.delete_tavus_identity.assert_not_called()
        with psycopg.connect(url) as connection:
            row = connection.execute('SELECT cleanup_requested, completed_at FROM avatar_runtime_cleanup WHERE session_id = %s', (session,)).fetchone()
            assert row == (True, None)
            connection.execute('UPDATE avatar_runtime_cleanup SET completed_at = NOW() WHERE session_id = %s', (session,))
        asyncio.run(service._run(current))
        assert repository.get(profile) is None
        assert repository.get_profile_erasure_request(request_id=request['request_id'])['status'] == 'completed'
    finally:
        with psycopg.connect(url) as connection:
            connection.execute('DELETE FROM avatar_runtime_cleanup WHERE session_id = %s', (session,))
            connection.execute('DELETE FROM digital_human_profiles WHERE profile_id = %s', (profile,))
