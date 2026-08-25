from __future__ import annotations

import os
from typing import Optional
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

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
                    FROM profile_memberships
                    WHERE
                        user_id = %s
                        AND profile_id = %s
                    """,
                    (
                        user_id,
                        profile_id,
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

            connection.commit()

        return self._membership_from_row(row), True

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
