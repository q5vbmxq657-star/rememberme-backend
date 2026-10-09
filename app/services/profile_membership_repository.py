from __future__ import annotations

import os
import json
from typing import Optional
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from app.services.plan_access import access_decision, effective_plan

from app.models.profile_membership import (
    ProfileMembership,
    ProfileMembershipRole,
    ProfileMembershipStatus,
)


class ProfileMembershipRepositoryError(
    RuntimeError
):
    pass


class ProfileProvisioningConflictError(
    ProfileMembershipRepositoryError
):
    pass


class ProfileMembershipRepository:
    def __init__(
        self,
        database_url: Optional[str] = None,
    ) -> None:
        self.database_url = (
            database_url
            or os.getenv("DATABASE_URL")
            or ""
        ).strip()

        if not self.database_url:
            raise ProfileMembershipRepositoryError(
                "DATABASE_URL is missing."
            )

    def get(
        self,
        *,
        user_id: UUID,
        profile_id: UUID,
        session_id: UUID | None = None,
    ) -> ProfileMembership | None:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        membership_id,
                        user_id,
                        profile_id,
                        role,
                        status,
                        created_at,
                        updated_at
                    FROM profile_memberships AS membership
                    WHERE
                        user_id = %s
                        AND profile_id = %s
                        AND EXISTS (
                            SELECT 1 FROM users
                            WHERE users.user_id = membership.user_id
                              AND users.status = 'active'
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM digital_human_profile_erasure_requests AS erasure
                            WHERE erasure.profile_id = membership.profile_id
                        )
                        AND (
                            %s::uuid IS NULL OR EXISTS (
                                SELECT 1 FROM user_sessions AS session
                                WHERE session.session_id = %s::uuid
                                  AND session.user_id = membership.user_id
                                  AND session.revoked_at IS NULL
                                  AND session.access_expires_at > NOW()
                                  AND session.refresh_expires_at > NOW()
                            )
                        )
                    """,
                    (
                        user_id,
                        profile_id,
                        session_id,
                        session_id,
                    ),
                )

                row = cursor.fetchone()

        if row is None:
            return None

        return self._membership_from_row(
            row
        )

    def provision_owned_profile(
        self,
        *,
        user_id: UUID,
        profile_id: UUID,
        consent_verified: bool,
        display_name: str | None = None,
        relationship: str | None = None,
    ) -> tuple[ProfileMembership, bool]:
        """Create a new profile and its owner membership atomically.

        Existing profiles can only be replayed by their current active owner.
        In particular, an unowned legacy profile is never claimed implicitly.
        """
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                # Serialize the limit check and creation across different profile IDs.
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                               ("plan-profile:" + str(user_id),))
                cursor.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s, 0)
                    )
                    """,
                    (str(profile_id),),
                )

                cursor.execute(
                    """
                    SELECT profile_id
                    FROM digital_human_profiles
                    WHERE profile_id = %s
                    FOR UPDATE
                    """,
                    (profile_id,),
                )

                profile_existed = (
                    cursor.fetchone() is not None
                )

                if not profile_existed:
                    cursor.execute("""SELECT COUNT(*) AS used FROM profile_memberships m
                        WHERE m.user_id=%s AND m.role='owner' AND m.status='active'
                        AND NOT EXISTS (SELECT 1 FROM digital_human_profile_erasure_requests e
                            WHERE e.profile_id=m.profile_id)""", (user_id,))
                    used = cursor.fetchone()["used"]
                    decision = access_decision(plan=effective_plan(cursor, user_id),
                                               action="create_avatar", used=used)
                    if not decision["allowed"]:
                        raise HTTPException(402, detail=decision)
                    cursor.execute(
                        """
                        INSERT INTO digital_human_profiles (
                            profile_id,
                            consent_verified
                        )
                        VALUES (%s, %s)
                        """,
                        (
                            profile_id,
                            consent_verified,
                        ),
                    )

                cursor.execute(
                    """
                    SELECT
                        membership_id,
                        user_id,
                        profile_id,
                        role,
                        status,
                        created_at,
                        updated_at
                    FROM profile_memberships
                    WHERE profile_id = %s
                    FOR UPDATE
                    """,
                    (profile_id,),
                )

                memberships = cursor.fetchall()
                current = next(
                    (
                        row
                        for row in memberships
                        if row["user_id"] == user_id
                        and row["role"] == "owner"
                        and row["status"] == "active"
                    ),
                    None,
                )

                if profile_existed:
                    if current is None:
                        raise ProfileProvisioningConflictError(
                            "Profile cannot be claimed."
                        )

                    self._save_missing_identity(cursor, profile_id, display_name, relationship)

                    return (
                        self._membership_from_row(current),
                        False,
                    )

                if memberships:
                    raise ProfileProvisioningConflictError(
                        "Profile ownership is inconsistent."
                    )

                membership_id = uuid4()

                cursor.execute(
                    """
                    INSERT INTO profile_memberships (
                        membership_id,
                        user_id,
                        profile_id,
                        role,
                        status
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        'owner',
                        'active'
                    )
                    RETURNING
                        membership_id,
                        user_id,
                        profile_id,
                        role,
                        status,
                        created_at,
                        updated_at
                    """,
                    (
                        membership_id,
                        user_id,
                        profile_id,
                    ),
                )

                row = cursor.fetchone()

                if row is None:
                    raise ProfileMembershipRepositoryError(
                        "Profile ownership could not be created."
                    )

                self._save_missing_identity(cursor, profile_id, display_name, relationship)

            connection.commit()

        return self._membership_from_row(row), True

    @staticmethod
    def _save_missing_identity(cursor, profile_id, display_name, relationship):
        # Never replace a known identity during a provisioning replay.
        if not display_name or not display_name.strip() or display_name.strip() == "Recovered memory space":
            return
        cursor.execute("""UPDATE digital_human_profiles
            SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
            WHERE profile_id = %s AND (
                NULLIF(BTRIM(metadata->>'display_name'), '') IS NULL
                OR metadata->>'display_name' = 'Recovered memory space')""",
            (json.dumps({"display_name": display_name.strip(),
                         "relationship": (relationship or "").strip()}), profile_id))

    def account_directory(self, *, user_id: UUID, session_id: UUID) -> list[dict]:
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            rows = connection.execute("""
                SELECT p.profile_id, p.created_at,
                    NULLIF(NULLIF(BTRIM(p.metadata->>'display_name'), ''), 'Recovered memory space') AS display_name,
                    p.metadata->>'relationship' AS relationship
                FROM digital_human_profiles p
                JOIN profile_memberships m ON m.profile_id = p.profile_id
                JOIN users u ON u.user_id = m.user_id AND u.status = 'active'
                JOIN user_sessions s ON s.user_id = u.user_id AND s.session_id = %s
                WHERE m.user_id = %s AND m.role = 'owner' AND m.status = 'active'
                    AND s.revoked_at IS NULL AND s.access_expires_at > NOW()
                    AND s.refresh_expires_at > NOW()
                    AND NOT EXISTS (SELECT 1 FROM digital_human_profile_erasure_requests e
                                    WHERE e.profile_id = p.profile_id)
                ORDER BY p.created_at DESC, p.profile_id
                """, (session_id, user_id)).fetchall()
        return [{**row, "profile_id": str(row["profile_id"]),
                 "created_at": row["created_at"].isoformat()} for row in rows]

    def list_active_for_user(
        self,
        *,
        user_id: UUID,
    ) -> list[ProfileMembership]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        membership_id,
                        user_id,
                        profile_id,
                        role,
                        status,
                        created_at,
                        updated_at
                    FROM profile_memberships
                    WHERE
                        user_id = %s
                        AND status = 'active'
                    ORDER BY created_at ASC
                    """,
                    (user_id,),
                )

                rows = cursor.fetchall()

        return [
            self._membership_from_row(
                row
            )
            for row in rows
        ]

    def list_owned_for_user(
        self,
        *,
        user_id: UUID,
    ) -> list[ProfileMembership]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        membership_id, user_id, profile_id, role, status,
                        created_at, updated_at
                    FROM profile_memberships
                    WHERE user_id = %s AND role = 'owner'
                    ORDER BY created_at ASC
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()

        return [self._membership_from_row(row) for row in rows]

    @staticmethod
    def _membership_from_row(
        row: dict,
    ) -> ProfileMembership:
        return ProfileMembership(
            membership_id=(
                row["membership_id"]
            ),
            user_id=row["user_id"],
            profile_id=row["profile_id"],
            role=ProfileMembershipRole(
                row["role"]
            ),
            status=ProfileMembershipStatus(
                row["status"]
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
