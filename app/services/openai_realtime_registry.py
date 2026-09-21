"""Persistent call ownership and cleanup intent; no SDP, secrets or memory text."""
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class RealtimeStateConflict(RuntimeError):
    pass


class OpenAIRealtimeRegistry:
    def __init__(self, database_url=None):
        self.database_url = database_url or os.environ['DATABASE_URL']

    def execute(self, sql, args=()):
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            cursor = connection.execute(sql, args)
            return cursor.fetchone() if cursor.description else None

    def reserve(self, *, profile_id, user_id, auth_session_id, purpose_revision, model, voice, metadata):
        return self.execute('''INSERT INTO openai_realtime_calls
            (session_id,profile_id,user_id,auth_session_id,purpose_revision,model,voice,metadata,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW()+INTERVAL '2 minutes') RETURNING *''',
            (uuid4(), profile_id, user_id, auth_session_id, purpose_revision, model, voice, Jsonb(metadata)))

    def owned(self, session_id, principal):
        return self.execute('''SELECT * FROM openai_realtime_calls
            WHERE session_id=%s AND user_id=%s AND auth_session_id=%s''',
            (session_id, principal.user.user_id, principal.session_id))

    def begin(self, session_id, memory_version):
        row = self.execute('''UPDATE openai_realtime_calls SET state='creating',
            create_started_at=NOW(), memory_version=%s, metadata='{}'::jsonb,
            expires_at=NOW()+INTERVAL '60 minutes'
            WHERE session_id=%s AND state='reserved' AND hangup_requested_at IS NULL
              AND expires_at>NOW() RETURNING *''', (Jsonb(memory_version), session_id))
        if not row:
            raise RealtimeStateConflict('This call cannot be started again.')
        return row

    def register(self, session_id, call_id):
        row = self.execute('''UPDATE openai_realtime_calls SET call_id=%s,
            state=CASE WHEN hangup_requested_at IS NULL THEN 'active' ELSE 'hangup_requested' END
            WHERE session_id=%s AND create_started_at IS NOT NULL AND call_id IS NULL
            RETURNING *''', (call_id, session_id))
        if not row:
            raise RealtimeStateConflict('Call registration failed.')
        return row

    def unknown(self, session_id):
        self.execute('''UPDATE openai_realtime_calls SET state='creation_unknown',
            hangup_requested_at=COALESCE(hangup_requested_at,NOW())
            WHERE session_id=%s AND create_started_at IS NOT NULL AND call_id IS NULL''', (session_id,))

    def request(self, session_id):
        return self.execute('''UPDATE openai_realtime_calls SET
            hangup_requested_at=COALESCE(hangup_requested_at,NOW()), retry_at=NOW(), metadata='{}'::jsonb,
            state=CASE WHEN state='reserved' THEN 'cancelled'
                WHEN hangup_acknowledged_at IS NOT NULL THEN 'hangup_acknowledged'
                WHEN call_id IS NULL THEN 'creation_unknown' ELSE 'hangup_requested' END
            WHERE session_id=%s RETURNING *''', (session_id,))

    def sweep(self):
        # The same durable predicate covers revocations on other devices and process restarts.
        self.execute('''UPDATE openai_realtime_calls r SET
            hangup_requested_at=COALESCE(r.hangup_requested_at,NOW()), metadata='{}'::jsonb,
            state=CASE WHEN r.state='reserved' THEN 'cancelled'
                WHEN r.call_id IS NULL THEN 'creation_unknown' ELSE 'hangup_requested' END
            WHERE r.hangup_acknowledged_at IS NULL AND r.hangup_requested_at IS NULL AND (
                r.expires_at<=NOW()
                OR (r.state='creating' AND r.create_started_at<NOW()-INTERVAL '2 minutes')
                OR EXISTS (SELECT 1 FROM digital_human_profile_erasure_requests e WHERE e.profile_id=r.profile_id)
                OR NOT EXISTS (SELECT 1 FROM profile_memberships m JOIN users u ON u.user_id=m.user_id
                    JOIN user_sessions s ON s.user_id=u.user_id
                    WHERE m.profile_id=r.profile_id AND m.user_id=r.user_id AND m.status='active'
                      AND u.status='active' AND s.session_id=r.auth_session_id
                      AND s.revoked_at IS NULL AND s.access_expires_at>NOW() AND s.refresh_expires_at>NOW())
                OR NOT EXISTS (SELECT 1 FROM profile_purpose_consents c WHERE c.profile_id=r.profile_id
                    AND c.revision=r.purpose_revision AND c.policy_version='avatar-consent-v1'
                    AND c.purposes @> ARRAY['memory_context','provider_processing']::text[])
                OR (r.create_started_at IS NOT NULL AND r.memory_version IS DISTINCT FROM
                    COALESCE((SELECT jsonb_build_array(g.generation,g.published_generation,g.operation_id::text)
                        FROM memory_index_generations g WHERE g.profile_id=r.profile_id),'null'::jsonb))
            )''')

    def request_profile(self, profile_id):
        self.execute('''UPDATE openai_realtime_calls SET
            hangup_requested_at=COALESCE(hangup_requested_at,NOW()), retry_at=NOW(), metadata='{}'::jsonb,
            state=CASE WHEN state='reserved' THEN 'cancelled'
                WHEN hangup_acknowledged_at IS NOT NULL THEN 'hangup_acknowledged'
                WHEN call_id IS NULL THEN 'creation_unknown' ELSE 'hangup_requested' END
            WHERE profile_id=%s''', (profile_id,))

    def profile_cleanup_status(self, profile_id):
        row = self.execute('''SELECT
            COUNT(*) FILTER (WHERE create_started_at IS NOT NULL) AS termination_unconfirmed,
            COUNT(*) FILTER (WHERE create_started_at IS NOT NULL AND call_id IS NULL) AS creation_unknown,
            COUNT(*) FILTER (WHERE hangup_acknowledged_at IS NOT NULL) AS hangup_acknowledged
            FROM openai_realtime_calls WHERE profile_id=%s''', (profile_id,))
        # No documented provider terminal-state evidence is available in this integration yet.
        # A successful hangup request must not silently satisfy a confirmed-termination gate.
        return {**row, 'confirmed_terminated': row['termination_unconfirmed'] == 0}

    def require_profile_terminated(self, profile_id):
        self.request_profile(profile_id)
        if not self.profile_cleanup_status(profile_id)['confirmed_terminated']:
            raise RealtimeStateConflict('OpenAI call termination has not been confirmed.')

    def claim(self):
        return self.execute('''WITH candidate AS (
            SELECT session_id FROM openai_realtime_calls WHERE hangup_requested_at IS NOT NULL
              AND hangup_acknowledged_at IS NULL AND call_id IS NOT NULL AND retry_at<=NOW()
              AND (lease_until IS NULL OR lease_until<=NOW())
            ORDER BY retry_at FOR UPDATE SKIP LOCKED LIMIT 1)
            UPDATE openai_realtime_calls r SET lease_token=%s, lease_until=NOW()+INTERVAL '90 seconds',
                attempts=attempts+1 FROM candidate c WHERE r.session_id=c.session_id RETURNING r.*''', (uuid4(),))

    def finish(self, row, acknowledged):
        self.execute('''UPDATE openai_realtime_calls SET
            hangup_acknowledged_at=CASE WHEN %s THEN NOW() ELSE hangup_acknowledged_at END,
            state=CASE WHEN %s OR hangup_acknowledged_at IS NOT NULL
                THEN 'hangup_acknowledged' ELSE 'hangup_requested' END,
            retry_at=NOW()+LEAST(300,5*attempts)*INTERVAL '1 second', lease_until=NULL, lease_token=NULL
            WHERE session_id=%s AND lease_token=%s''',
            (acknowledged, acknowledged, row['session_id'], row['lease_token']))

    def acknowledge(self, session_id, call_id):
        self.execute('''UPDATE openai_realtime_calls SET
            hangup_acknowledged_at=COALESCE(hangup_acknowledged_at,NOW()), state='hangup_acknowledged'
            WHERE session_id=%s AND call_id=%s AND hangup_requested_at IS NOT NULL''',
            (session_id, call_id))
