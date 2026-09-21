import os
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from app.services.digital_human_profile_repository import DigitalHumanProfileRepository
from app.services.openai_realtime_registry import RealtimeStateConflict
from app.services.profile_erasure_service import ProfileErasureService


@pytest.mark.parametrize('state,started,handle,blocked', [
    ('reserved', False, False, False), ('cancelled', False, False, False),
    ('creating', True, False, True), ('creation_unknown', True, False, True),
    ('active', True, True, True), ('hangup_acknowledged', True, True, True),
])
def test_profile_erasure_requests_openai_cleanup_and_requires_terminal_proof(state, started, handle, blocked):
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated AP1 PostgreSQL')
    config = conninfo_to_dict(url)
    assert config.get('host', '').startswith('/private/tmp/STAY-AP1-PG-')
    assert config.get('dbname') == 'stay_ap1'
    profile, session = uuid4(), uuid4()
    with psycopg.connect(url) as connection:
        connection.execute("""INSERT INTO openai_realtime_calls
            (session_id, profile_id, user_id, auth_session_id, purpose_revision, model, voice,
             state, expires_at, create_started_at, call_id, hangup_acknowledged_at)
            VALUES (%s, %s, %s, %s, 1, 'test', 'test', %s, NOW(),
                CASE WHEN %s THEN NOW() ELSE NULL END, %s,
                CASE WHEN %s = 'hangup_acknowledged' THEN NOW() ELSE NULL END)""",
            (session, profile, uuid4(), uuid4(), state, started, str(uuid4()) if handle else None, state))
    runtime = Mock()
    runtime._execute.return_value = None
    service = ProfileErasureService(repository=DigitalHumanProfileRepository(database_url=url),
        runtime_cleanup_repository=runtime, avatar_provider=AsyncMock(),
        voice_service=AsyncMock(), media_storage=Mock())
    try:
        if blocked:
            with pytest.raises(RealtimeStateConflict):
                service._require_runtime_cleanup(profile)
        else:
            service._require_runtime_cleanup(profile)
        with psycopg.connect(url) as connection:
            assert connection.execute('SELECT hangup_requested_at IS NOT NULL FROM openai_realtime_calls WHERE session_id = %s', (session,)).fetchone()[0]
    finally:
        with psycopg.connect(url) as connection:
            connection.execute('DELETE FROM openai_realtime_calls WHERE session_id = %s', (session,))
