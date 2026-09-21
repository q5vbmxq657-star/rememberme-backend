"""Durable provider handles; never store media, credentials or call content."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


class RuntimeCleanupRepository:
    def __init__(self, database_url=None):
        self.database_url = database_url or os.environ["DATABASE_URL"]

    def _execute(self, sql, args=(), *, all_rows=False):
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            cursor = connection.execute(sql, args)
            if cursor.description:
                return cursor.fetchall() if all_rows else cursor.fetchone()

    def register(self, session_id, profile_id, room_name, expires_at, purpose_revision, purposes):
        self._execute("""INSERT INTO avatar_runtime_cleanup
            (session_id, profile_id, room_name, expires_at, purpose_revision, purposes, conversation_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (session_id, profile_id, room_name, expires_at, purpose_revision, sorted(purposes), 'stay_' + uuid4().hex))

    def material_purposes(self, profile_id, face_id):
        row = self._execute("""SELECT request_payload FROM digital_human_training_jobs
            WHERE profile_id=%s AND provider='tavus' AND provider_job_id=%s
            ORDER BY created_at DESC LIMIT 1""", (profile_id, 'tavus:' + face_id))
        payload = row['request_payload'] if row else {}
        if payload.get('train_image_url'):
            return {'photo_likeness'}
        if payload.get('train_video_url'):
            return {'video_motion'}
        raise RuntimeError('Avatar training source cannot be verified.')

    def authorize(self, session_id):
        from app.security.purpose_authorization import require_profile_purposes
        row = self.get(session_id)
        if not row or row['cleanup_requested'] or row['completed_at'] or row['expires_at'] <= datetime.now(timezone.utc):
            raise RuntimeError('Runtime session is closing.')
        erasure = self._execute('SELECT 1 FROM digital_human_profile_erasure_requests WHERE profile_id=%s LIMIT 1',
                                (row['profile_id'],))
        if erasure:
            raise RuntimeError('Profile erasure is pending.')
        require_profile_purposes(row['profile_id'], set(row['purposes']),
                                expected_revision=row['purpose_revision'])
        return row

    def get(self, session_id):
        return self._execute("SELECT * FROM avatar_runtime_cleanup WHERE session_id=%s", (session_id,))

    def dispatch(self, session_id, dispatch_id):
        self._execute("UPDATE avatar_runtime_cleanup SET dispatch_id=%s WHERE session_id=%s",
                      (dispatch_id, session_id))

    def begin_worker(self, session_id, profile_id, room_name):
        row = self._execute("""UPDATE avatar_runtime_cleanup SET worker_started=TRUE
            WHERE session_id=%s AND profile_id=%s AND room_name=%s AND NOT worker_started
            AND NOT cleanup_requested AND completed_at IS NULL AND expires_at>NOW()
            RETURNING session_id""", (session_id, profile_id, room_name))
        if not row:
            raise RuntimeError("Runtime session no longer permits worker activation.")

    def conversation(self, session_id, conversation_id):
        if not conversation_id:
            raise RuntimeError("Remote conversation identity is unavailable.")
        row = self._execute("""UPDATE avatar_runtime_cleanup SET conversation_id=%s
            WHERE session_id=%s AND worker_started AND completed_at IS NULL
            AND (conversation_id IS NULL OR conversation_id=%s) RETURNING session_id""",
            (conversation_id, session_id, conversation_id))
        if not row:
            raise RuntimeError("Remote conversation could not be registered.")

    def begin_provider_create(self, session_id):
        row = self._execute("""UPDATE avatar_runtime_cleanup SET provider_create_started=TRUE
            WHERE session_id=%s AND worker_started AND NOT provider_create_started
            AND NOT cleanup_requested AND completed_at IS NULL AND expires_at>NOW()
            AND conversation_name IS NOT NULL RETURNING conversation_name""", (session_id,))
        if not row:
            raise RuntimeError('Remote creation is no longer permitted.')
        return row['conversation_name']

    def ended(self, session_id, conversation_id):
        self._execute("""UPDATE avatar_runtime_cleanup SET tavus_ended=TRUE
            WHERE session_id=%s AND conversation_id=%s""", (session_id, conversation_id))

    def request(self, session_id):
        self._execute("""UPDATE avatar_runtime_cleanup SET cleanup_requested=TRUE, retry_at=NOW()
            WHERE session_id=%s AND completed_at IS NULL""", (session_id,))

    def deleted(self, session_id, conversation_id):
        row = self._execute("""UPDATE avatar_runtime_cleanup SET conversation_deleted=TRUE
            WHERE session_id=%s AND conversation_id=%s AND tavus_ended
            RETURNING session_id""", (session_id, conversation_id))
        if not row:
            raise RuntimeError('Conversation deletion cannot be acknowledged.')

    def request_profile(self, profile_id):
        self._execute("""UPDATE avatar_runtime_cleanup SET cleanup_requested=TRUE, retry_at=NOW()
            WHERE profile_id=%s AND completed_at IS NULL""", (profile_id,))

    def claim(self):
        self._execute("""UPDATE avatar_runtime_cleanup r SET cleanup_requested=TRUE
            WHERE completed_at IS NULL AND NOT cleanup_requested AND EXISTS (
                SELECT 1 FROM digital_human_profile_erasure_requests e WHERE e.profile_id=r.profile_id)""")
        self._execute("""UPDATE avatar_runtime_cleanup r SET cleanup_requested=TRUE
            WHERE completed_at IS NULL AND NOT cleanup_requested AND NOT EXISTS (
                SELECT 1 FROM profile_purpose_consents c WHERE c.profile_id=r.profile_id
                AND c.revision=r.purpose_revision AND c.purposes @> r.purposes
                AND c.purposes @> ARRAY['provider_processing']::text[])""")
        return self._execute("""WITH candidate AS (
            SELECT session_id FROM avatar_runtime_cleanup
            WHERE completed_at IS NULL AND (cleanup_requested OR expires_at<=NOW())
              AND retry_at<=NOW() AND (lease_until IS NULL OR lease_until<=NOW())
            ORDER BY retry_at FOR UPDATE SKIP LOCKED LIMIT 1
        ) UPDATE avatar_runtime_cleanup r SET cleanup_requested=TRUE,
            lease_until=NOW()+INTERVAL '3 minutes', lease_token=%s, attempts=attempts+1
            FROM candidate c WHERE r.session_id=c.session_id RETURNING r.*""", (uuid4(),))

    def finish(self, row, success):
        self._execute("""UPDATE avatar_runtime_cleanup SET
            completed_at=CASE WHEN %s AND (
                (NOT provider_create_started AND conversation_id IS NULL)
                OR (tavus_ended AND conversation_deleted AND conversation_id IS NOT NULL)
            ) THEN NOW() ELSE NULL END,
            retry_at=NOW()+LEAST(300, 5 * attempts)*INTERVAL '1 second',
            lease_until=NULL, lease_token=NULL
            WHERE session_id=%s AND lease_token=%s""",
            (success, row["session_id"], row["lease_token"]))
