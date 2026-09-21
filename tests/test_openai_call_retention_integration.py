import os
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from app.services.deletion_retention_service import DeletionRetentionService


def test_call_retention_never_discards_unconfirmed_provider_handles():
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated AP1 PostgreSQL')
    config = conninfo_to_dict(url)
    assert config.get('host', '').startswith('/private/tmp/STAY-AP1-PG-')
    assert config.get('dbname') == 'stay_ap1'
    cases = [('cancelled', False, 31), ('cancelled', False, 1),
             ('hangup_acknowledged', True, 31), ('creation_unknown', True, 31),
             ('active', True, 31)]
    identifiers = [uuid4() for _ in cases]
    with psycopg.connect(url) as connection:
        for identifier, (state, started, days) in zip(identifiers, cases):
            connection.execute("""INSERT INTO openai_realtime_calls
                (session_id, profile_id, user_id, auth_session_id, purpose_revision, model, voice,
                 metadata, state, call_id, expires_at, create_started_at, hangup_requested_at,
                 hangup_acknowledged_at)
                VALUES (%s, %s, %s, %s, 1, 'test-model', 'test-voice',
                 '{"profile_name":"private"}', %s, %s, NOW(),
                 CASE WHEN %s THEN NOW() - INTERVAL '32 days' ELSE NULL END,
                 NOW() - %s * INTERVAL '1 day',
                 CASE WHEN %s = 'hangup_acknowledged' THEN NOW() - INTERVAL '31 days' ELSE NULL END)""",
                (identifier, uuid4(), uuid4(), uuid4(), state,
                 str(uuid4()) if state in {'active', 'hangup_acknowledged'} else None, started, days, state))
    try:
        DeletionRetentionService(database_url=url).purge_completed_cleanup_records()
        with psycopg.connect(url) as connection:
            rows = connection.execute('SELECT session_id, metadata FROM openai_realtime_calls WHERE session_id = ANY(%s)', (identifiers,)).fetchall()
        assert {row[0] for row in rows} == set(identifiers[1:])
        assert all(row[1] == {} for row in rows)
    finally:
        with psycopg.connect(url) as connection:
            connection.execute('DELETE FROM openai_realtime_calls WHERE session_id = ANY(%s)', (identifiers,))
