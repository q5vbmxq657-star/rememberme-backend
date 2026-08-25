from __future__ import annotations

import os
from datetime import (
    datetime,
    timezone,
)
from uuid import (
    UUID,
    uuid4,
)

import psycopg
from psycopg.rows import dict_row

from app.models.user_session import UserSession
from app.security.session_tokens import (
    SessionTokenAuthority,
    SessionTokenPair,
    hash_refresh_token,
)


class UserSessionRepositoryError(
    RuntimeError
):
    pass


class InvalidRefreshCredentialError(
    UserSessionRepositoryError
):
    pass


class UserSessionRepository:
    def __init__(
        self,
        *,
        database_url: str | None = None,
        token_authority: SessionTokenAuthority | None = None,
    ) -> None:
        self.database_url = (
            database_url
            or os.getenv(
                "DATABASE_URL"
            )
            or ""
        ).strip()

        if not self.database_url:
            raise UserSessionRepositoryError(
                "Database configuration is unavailable."
            )

        self.token_authority = (
            token_authority
            or SessionTokenAuthority()
        )

    def create_session(
        self,
        *,
        user_id: UUID,
    ) -> tuple[
        UserSession,
        SessionTokenPair,
    ]:
        session_id = uuid4()

        token_pair = (
            self.token_authority.issue(
                user_id=user_id,
                session_id=session_id,
            )
        )

        refresh_hash = hash_refresh_token(
            token_pair.refresh_token
        )

        try:
            with psycopg.connect(
                self.database_url,
                connect_timeout=10,
                row_factory=dict_row,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO user_sessions (
                            session_id,
                            user_id,
                            refresh_token_hash,
                            access_expires_at,
                            refresh_expires_at
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        RETURNING *
                        """,
                        (
                            session_id,
                            user_id,
                            refresh_hash,
                            token_pair.access_expires_at,
                            token_pair.refresh_expires_at,
                        ),
                    )

                    row = cursor.fetchone()

                connection.commit()

        except psycopg.Error as error:
            raise UserSessionRepositoryError(
                "Session persistence failed."
            ) from error

        if row is None:
            raise UserSessionRepositoryError(
                "Session persistence failed."
            )

        return (
            self._session_from_row(
                row
            ),
            token_pair,
        )

    def rotate_refresh_token(
        self,
        *,
        refresh_token: str,
    ) -> tuple[
        UserSession,
        SessionTokenPair,
    ]:
        supplied_hash = hash_refresh_token(
            refresh_token
        )

        try:
            with psycopg.connect(
                self.database_url,
                connect_timeout=10,
                row_factory=dict_row,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT *
                        FROM user_sessions
                        WHERE refresh_token_hash = %s
                        FOR UPDATE
                        """,
                        (
                            supplied_hash,
                        ),
                    )

                    row = cursor.fetchone()

                    if row is None:
                        raise InvalidRefreshCredentialError(
                            "Refresh credential is invalid."
                        )

                    session = self._session_from_row(
                        row
                    )

                    now = datetime.now(
                        timezone.utc
                    )

                    if (
                        session.revoked_at is not None
                        or session.refresh_expires_at <= now
                    ):
                        raise InvalidRefreshCredentialError(
                            "Refresh credential is invalid."
                        )

                    next_pair = (
                        self.token_authority.issue(
                            user_id=session.user_id,
                            session_id=session.session_id,
                            now=now,
                        )
                    )

                    next_hash = hash_refresh_token(
                        next_pair.refresh_token
                    )

                    cursor.execute(
                        """
                        UPDATE user_sessions
                        SET
                            refresh_token_hash = %s,
                            access_expires_at = %s,
                            refresh_expires_at = %s,
                            last_rotated_at = NOW(),
                            updated_at = NOW()
                        WHERE
                            session_id = %s
                            AND refresh_token_hash = %s
                            AND revoked_at IS NULL
                        RETURNING *
                        """,
                        (
                            next_hash,
                            next_pair.access_expires_at,
                            next_pair.refresh_expires_at,
                            session.session_id,
                            supplied_hash,
                        ),
                    )

                    updated = cursor.fetchone()

                    if updated is None:
                        raise InvalidRefreshCredentialError(
                            "Refresh credential is invalid."
                        )

                connection.commit()

        except InvalidRefreshCredentialError:
            raise

        except psycopg.Error as error:
            raise UserSessionRepositoryError(
                "Session refresh failed."
            ) from error

        return (
            self._session_from_row(
                updated
            ),
            next_pair,
        )

    def get_active_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
    ) -> UserSession | None:
        try:
            with psycopg.connect(
                self.database_url,
                connect_timeout=10,
                row_factory=dict_row,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT *
                        FROM user_sessions
                        WHERE
                            session_id = %s
                            AND user_id = %s
                            AND revoked_at IS NULL
                        """,
                        (
                            session_id,
                            user_id,
                        ),
                    )

                    row = cursor.fetchone()

        except psycopg.Error as error:
            raise UserSessionRepositoryError(
                "Session lookup failed."
            ) from error

        if row is None:
            return None

        return self._session_from_row(
            row
        )

    def revoke_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
    ) -> bool:
        try:
            with psycopg.connect(
                self.database_url,
                connect_timeout=10,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE user_sessions
                        SET
                            revoked_at = COALESCE(
                                revoked_at,
                                NOW()
                            ),
                            updated_at = NOW()
                        WHERE
                            session_id = %s
                            AND user_id = %s
                        """,
                        (
                            session_id,
                            user_id,
                        ),
                    )

                    changed = (
                        cursor.rowcount > 0
                    )

                connection.commit()

        except psycopg.Error as error:
            raise UserSessionRepositoryError(
                "Session revocation failed."
            ) from error

        return changed

    @staticmethod
    def _session_from_row(
        row: dict,
    ) -> UserSession:
        return UserSession(
            session_id=row["session_id"],
            user_id=row["user_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            access_expires_at=row["access_expires_at"],
            refresh_expires_at=row["refresh_expires_at"],
            revoked_at=row["revoked_at"],
            last_rotated_at=row["last_rotated_at"],
        )
