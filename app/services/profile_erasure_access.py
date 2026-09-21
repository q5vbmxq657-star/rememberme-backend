from datetime import datetime, timezone
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row


class ErasureAccessDenied(PermissionError):
    pass


class ProfileErasureAccess:
    def __init__(self, repository):
        self.repository = repository

    def authorize_request(self, principal, profile_id: UUID) -> dict:
        if principal.access_expires_at <= datetime.now(timezone.utc):
            raise ErasureAccessDenied()
        user_id = principal.user.user_id
        key = f'profile-delete:v1:{user_id}:{profile_id}'
        with psycopg.connect(self.repository.database_url, row_factory=dict_row) as connection:
            session = connection.execute("""SELECT s.session_id FROM user_sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.session_id = %s AND s.user_id = %s AND u.status = 'active'
                  AND s.revoked_at IS NULL AND s.access_expires_at > NOW()
                  AND s.refresh_expires_at > NOW() FOR SHARE OF s, u""",
                (principal.session_id, user_id)).fetchone()
            if session is None:
                raise ErasureAccessDenied()
            request = connection.execute("""SELECT e.* FROM profile_erasure_receipts r
                JOIN digital_human_profile_erasure_requests e USING(request_id)
                WHERE r.user_id = %s AND r.profile_id = %s""", (user_id, profile_id)).fetchone()
            if request:
                return dict(request)
            request = connection.execute("""SELECT * FROM digital_human_profile_erasure_requests
                WHERE idempotency_key = %s""", (key,)).fetchone()
            if request:
                if request['profile_id'] is not None and request['profile_id'] != profile_id:
                    raise ErasureAccessDenied()
                return dict(request)
            membership = connection.execute("""SELECT membership_id FROM profile_memberships
                WHERE user_id = %s AND profile_id = %s AND status = 'active' AND role = 'owner'
                FOR SHARE""", (user_id, profile_id)).fetchone()
            if membership is None:
                raise ErasureAccessDenied()
            # Keep membership and session proof locked until the canonical request commits.
            request = self.repository.create_profile_erasure_request(request_id=uuid4(),
                profile_id=profile_id, idempotency_key=key)
            connection.execute("""INSERT INTO profile_erasure_receipts(user_id, profile_id, request_id)
                VALUES (%s, %s, %s) ON CONFLICT (user_id, profile_id) DO NOTHING""",
                (user_id, profile_id, request['request_id']))
            return request
