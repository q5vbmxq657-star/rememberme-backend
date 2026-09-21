from __future__ import annotations

import os
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.schemas.profile_consent import CONSENT_POLICY_VERSION, PurposeConsentSnapshot, PurposeConsentUpdate


class ConsentAccessDenied(PermissionError):
    pass


class ConsentRevisionConflict(RuntimeError):
    pass


class ProfileConsentRepository:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.environ["DATABASE_URL"]

    def read(self, profile_id: UUID) -> PurposeConsentSnapshot:
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT consent.profile_id, consent.revision, consent.policy_version, consent.purposes
                   FROM profile_purpose_consents AS consent
                   WHERE consent.profile_id = %s AND NOT EXISTS (
                       SELECT 1 FROM digital_human_profile_erasure_requests AS erasure
                       WHERE erasure.profile_id = consent.profile_id)""",
                (profile_id,),
            ).fetchone()
        return self._snapshot(profile_id, row)

    def update(self, *, profile_id: UUID, user_id: UUID, session_id: UUID,
               update: PurposeConsentUpdate) -> PurposeConsentSnapshot:
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            # The profile lock also serializes grants with erasure and first-time grants.
            if connection.execute(
                "SELECT profile_id FROM digital_human_profiles WHERE profile_id = %s FOR UPDATE",
                (profile_id,),
            ).fetchone() is None:
                raise ConsentAccessDenied("Profile not found.")
            authorized = connection.execute("""
                SELECT membership.membership_id
                FROM profile_memberships AS membership
                JOIN users ON users.user_id = membership.user_id
                JOIN user_sessions AS session ON session.user_id = users.user_id
                WHERE membership.profile_id = %s AND membership.user_id = %s
                  AND membership.role = 'owner' AND membership.status = 'active'
                  AND users.status = 'active' AND session.session_id = %s
                  AND session.revoked_at IS NULL AND session.access_expires_at > NOW()
                  AND session.refresh_expires_at > NOW()
                  AND NOT EXISTS (SELECT 1 FROM digital_human_profile_erasure_requests
                                  WHERE profile_id = membership.profile_id)
                FOR SHARE OF membership, users, session
                """, (profile_id, user_id, session_id)).fetchone()
            if authorized is None:
                raise ConsentAccessDenied("Profile not found.")
            current = connection.execute(
                "SELECT profile_id, revision, policy_version, purposes FROM profile_purpose_consents WHERE profile_id = %s",
                (profile_id,),
            ).fetchone()
            snapshot = self._snapshot(profile_id, current)
            if snapshot.revision != update.expected_revision:
                raise ConsentRevisionConflict("Permissions changed. Refresh before trying again.")
            purposes = sorted(update.purposes)
            revision = snapshot.revision + 1
            connection.execute("""
                INSERT INTO profile_purpose_consents (profile_id, revision, policy_version, purposes)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (profile_id) DO UPDATE SET revision = EXCLUDED.revision,
                    policy_version = EXCLUDED.policy_version, purposes = EXCLUDED.purposes, updated_at = NOW()
                """, (profile_id, revision, update.policy_version, purposes))
            connection.execute("""
                INSERT INTO profile_purpose_consent_events
                    (profile_id, revision, actor_user_id, policy_version, purposes)
                VALUES (%s, %s, %s, %s, %s)
                """, (profile_id, revision, user_id, update.policy_version, purposes))
        return PurposeConsentSnapshot(profile_id=profile_id, revision=revision,
            policy_version=update.policy_version, purposes=purposes)

    @staticmethod
    def _snapshot(profile_id, row):
        if row is not None:
            return PurposeConsentSnapshot(**row)
        return PurposeConsentSnapshot(profile_id=profile_id, revision=0,
            policy_version=CONSENT_POLICY_VERSION, purposes=[])
