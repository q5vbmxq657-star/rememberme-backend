from __future__ import annotations

from datetime import datetime
import os
from typing import Optional
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.models.user_identity import (
    ExternalIdentityProvider,
    ExternalUserIdentity,
    UserIdentity,
    UserStatus,
)


class UserIdentityRepositoryError(RuntimeError):
    pass


class UserIdentityConflictError(
    UserIdentityRepositoryError
):
    pass


class UserIdentityRepository:
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
            raise UserIdentityRepositoryError(
                "DATABASE_URL is missing."
            )

    def get_user(
        self,
        *,
        user_id: UUID,
    ) -> UserIdentity | None:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        user_id,
                        status,
                        created_at,
                        updated_at
                    FROM users
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )

                row = cursor.fetchone()

        if row is None:
            return None

        return self._user_from_row(row)

    def get_apple_refresh_credential(
        self,
        *,
        user_id: UUID,
    ) -> bytes | None:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT provider_refresh_token_ciphertext
                    FROM external_user_identities
                    WHERE user_id = %s AND provider = 'apple'
                    FOR UPDATE
                    """,
                    (user_id,),
                )
                row = cursor.fetchone()

        if row is None:
            return None
        return row["provider_refresh_token_ciphertext"]

    def delete_user_after_profile_erasure(
        self,
        *,
        user_id: UUID,
    ) -> bool:
        """Delete identity and sessions only when no owned profile remains."""
        try:
            with psycopg.connect(
                self.database_url,
                connect_timeout=10,
                row_factory=dict_row,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT user_id FROM users WHERE user_id = %s FOR UPDATE",
                        (user_id,),
                    )
                    if cursor.fetchone() is None:
                        return True

                    cursor.execute(
                        """
                        SELECT 1 FROM profile_memberships
                        WHERE user_id = %s
                        LIMIT 1
                        """,
                        (user_id,),
                    )
                    if cursor.fetchone() is not None:
                        raise UserIdentityRepositoryError(
                            "Account still owns profile data."
                        )

                    cursor.execute(
                        "DELETE FROM users WHERE user_id = %s RETURNING user_id",
                        (user_id,),
                    )
                    deleted = cursor.fetchone() is not None
                connection.commit()
        except psycopg.Error as error:
            raise UserIdentityRepositoryError(
                "Account deletion failed."
            ) from error

        return deleted

    def get_external_identity(
        self,
        *,
        provider: ExternalIdentityProvider,
        provider_subject: str,
    ) -> ExternalUserIdentity | None:
        subject = provider_subject.strip()

        if not subject:
            raise ValueError(
                "provider_subject must not be empty."
            )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        identity_id,
                        user_id,
                        provider,
                        provider_subject,
                        email,
                        created_at,
                        updated_at
                    FROM external_user_identities
                    WHERE
                        provider = %s
                        AND provider_subject = %s
                    """,
                    (
                        provider.value,
                        subject,
                    ),
                )

                row = cursor.fetchone()

        if row is None:
            return None

        return self._external_identity_from_row(
            row
        )

    def resolve_or_create_external_identity(
        self,
        *,
        provider: ExternalIdentityProvider,
        provider_subject: str,
        email: str | None = None,
        provider_refresh_token_ciphertext: bytes,
        provider_credentials_verified_at: datetime,
    ) -> tuple[
        UserIdentity,
        ExternalUserIdentity,
        bool,
    ]:
        subject = provider_subject.strip()

        if not subject:
            raise ValueError(
                "provider_subject must not be empty."
            )

        normalized_email = (
            email.strip()
            if email is not None
            and email.strip()
            else None
        )

        if not provider_refresh_token_ciphertext:
            raise ValueError(
                "provider refresh credential must not be empty."
            )

        if (
            provider_credentials_verified_at
            .tzinfo is None
        ):
            raise ValueError(
                "provider credential verification time must be timezone-aware."
            )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                # Serialize the unique provider identity scope.
                #
                # INSERT ... ON CONFLICT followed by the
                # profile-independent identity lookup ensures
                # concurrent sign-ins converge onto exactly one
                # canonical internal user.
                candidate_user_id = uuid4()
                candidate_identity_id = uuid4()

                cursor.execute(
                    """
                    INSERT INTO users (
                        user_id
                    )
                    VALUES (%s)
                    """,
                    (candidate_user_id,),
                )

                cursor.execute(
                    """
                    INSERT INTO external_user_identities (
                        identity_id,
                        user_id,
                        provider,
                        provider_subject,
                        email,
                        provider_refresh_token_ciphertext,
                        provider_credentials_verified_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    ON CONFLICT (
                        provider,
                        provider_subject
                    )
                    DO NOTHING
                    RETURNING
                        identity_id,
                        user_id,
                        provider,
                        provider_subject,
                        email,
                        created_at,
                        updated_at
                    """,
                    (
                        candidate_identity_id,
                        candidate_user_id,
                        provider.value,
                        subject,
                        normalized_email,
                        provider_refresh_token_ciphertext,
                        provider_credentials_verified_at,
                    ),
                )

                identity_row = cursor.fetchone()

                created = (
                    identity_row is not None
                )

                if created:
                    cursor.execute(
                        """
                        SELECT
                            user_id,
                            status,
                            created_at,
                            updated_at
                        FROM users
                        WHERE user_id = %s
                        """,
                        (candidate_user_id,),
                    )

                    user_row = cursor.fetchone()

                else:
                    # The candidate user has no identity and
                    # therefore must not survive.
                    cursor.execute(
                        """
                        DELETE FROM users
                        WHERE user_id = %s
                        """,
                        (candidate_user_id,),
                    )

                    cursor.execute(
                        """
                        SELECT
                            identity_id,
                            user_id,
                            provider,
                            provider_subject,
                            email,
                            created_at,
                            updated_at
                        FROM external_user_identities
                        WHERE
                            provider = %s
                            AND provider_subject = %s
                        FOR UPDATE
                        """,
                        (
                            provider.value,
                            subject,
                        ),
                    )

                    identity_row = cursor.fetchone()

                    if identity_row is None:
                        raise (
                            UserIdentityRepositoryError(
                                "Canonical external identity "
                                "could not be resolved."
                            )
                        )

                    cursor.execute(
                        """
                        UPDATE external_user_identities
                        SET
                            email = COALESCE(
                                %s,
                                email
                            ),
                            provider_refresh_token_ciphertext = %s,
                            provider_credentials_verified_at = %s,
                            updated_at = NOW()
                        WHERE identity_id = %s
                        RETURNING
                            identity_id,
                            user_id,
                            provider,
                            provider_subject,
                            email,
                            created_at,
                            updated_at
                        """,
                        (
                            normalized_email,
                            provider_refresh_token_ciphertext,
                            provider_credentials_verified_at,
                            identity_row["identity_id"],
                        ),
                    )

                    identity_row = cursor.fetchone()

                    if identity_row is None:
                        raise UserIdentityRepositoryError(
                            "Canonical external identity "
                            "could not be updated."
                        )

                    existing_user_id = (
                        identity_row["user_id"]
                    )

                    cursor.execute(
                        """
                        SELECT
                            user_id,
                            status,
                            created_at,
                            updated_at
                        FROM users
                        WHERE user_id = %s
                        """,
                        (existing_user_id,),
                    )

                    user_row = cursor.fetchone()

                if (
                    identity_row is None
                    or user_row is None
                ):
                    raise UserIdentityRepositoryError(
                        "Canonical user identity "
                        "could not be resolved."
                    )

            connection.commit()

        user = self._user_from_row(
            user_row
        )

        identity = (
            self._external_identity_from_row(
                identity_row
            )
        )

        if identity.user_id != user.user_id:
            raise UserIdentityConflictError(
                "External identity/user binding "
                "is inconsistent."
            )

        return (
            user,
            identity,
            created,
        )

    @staticmethod
    def _user_from_row(
        row: dict,
    ) -> UserIdentity:
        return UserIdentity(
            user_id=row["user_id"],
            status=UserStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _external_identity_from_row(
        row: dict,
    ) -> ExternalUserIdentity:
        return ExternalUserIdentity(
            identity_id=row["identity_id"],
            user_id=row["user_id"],
            provider=(
                ExternalIdentityProvider(
                    row["provider"]
                )
            ),
            provider_subject=(
                row["provider_subject"]
            ),
            email=row["email"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
