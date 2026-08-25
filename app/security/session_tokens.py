from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from uuid import UUID

import jwt


ACCESS_TOKEN_ALGORITHM = "HS256"

DEFAULT_ACCESS_TTL_SECONDS = 900
DEFAULT_REFRESH_TTL_SECONDS = 2_592_000


class SessionTokenConfigurationError(
    RuntimeError
):
    pass


class SessionTokenVerificationError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class SessionTokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True)
class VerifiedSessionAccess:
    user_id: UUID
    session_id: UUID
    expires_at: datetime


class SessionTokenAuthority:
    def __init__(
        self,
        *,
        signing_secret: str | None = None,
        access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
        refresh_ttl_seconds: int = DEFAULT_REFRESH_TTL_SECONDS,
    ) -> None:
        resolved_secret = (
            signing_secret
            if signing_secret is not None
            else os.getenv(
                "STAY_SESSION_SIGNING_SECRET"
            )
        )

        self.signing_secret = (
            resolved_secret or ""
        ).strip()

        if len(self.signing_secret) < 32:
            raise SessionTokenConfigurationError(
                "Session signing secret is not configured safely."
            )

        if access_ttl_seconds <= 0:
            raise SessionTokenConfigurationError(
                "Access-token TTL must be positive."
            )

        if refresh_ttl_seconds <= access_ttl_seconds:
            raise SessionTokenConfigurationError(
                "Refresh-token TTL must exceed access-token TTL."
            )

        self.access_ttl_seconds = int(
            access_ttl_seconds
        )

        self.refresh_ttl_seconds = int(
            refresh_ttl_seconds
        )

    def issue(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        now: datetime | None = None,
    ) -> SessionTokenPair:
        current = (
            now
            if now is not None
            else datetime.now(
                timezone.utc
            )
        )

        access_expires_at = (
            current
            + timedelta(
                seconds=self.access_ttl_seconds
            )
        )

        refresh_expires_at = (
            current
            + timedelta(
                seconds=self.refresh_ttl_seconds
            )
        )

        access_token = jwt.encode(
            {
                "typ": "stay_access",
                "sub": str(user_id),
                "sid": str(session_id),
                "iat": int(
                    current.timestamp()
                ),
                "exp": int(
                    access_expires_at.timestamp()
                ),
            },
            self.signing_secret,
            algorithm=ACCESS_TOKEN_ALGORITHM,
        )

        refresh_token = secrets.token_urlsafe(
            48
        )

        return SessionTokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )

    def verify_access(
        self,
        token: str,
    ) -> VerifiedSessionAccess:
        credential = token.strip()

        if not credential:
            raise SessionTokenVerificationError(
                "Session credential is invalid."
            )

        try:
            claims = jwt.decode(
                credential,
                self.signing_secret,
                algorithms=[
                    ACCESS_TOKEN_ALGORITHM
                ],
                options={
                    "require": [
                        "typ",
                        "sub",
                        "sid",
                        "iat",
                        "exp",
                    ],
                    "verify_signature": True,
                    "verify_exp": True,
                },
            )

        except jwt.PyJWTError as error:
            raise SessionTokenVerificationError(
                "Session credential is invalid."
            ) from error

        if claims.get("typ") != "stay_access":
            raise SessionTokenVerificationError(
                "Session credential is invalid."
            )

        try:
            user_id = UUID(
                str(claims["sub"])
            )

            session_id = UUID(
                str(claims["sid"])
            )

            expires_at = datetime.fromtimestamp(
                int(claims["exp"]),
                tz=timezone.utc,
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise SessionTokenVerificationError(
                "Session credential is invalid."
            ) from error

        return VerifiedSessionAccess(
            user_id=user_id,
            session_id=session_id,
            expires_at=expires_at,
        )


def hash_refresh_token(
    token: str,
) -> str:
    clean = token.strip()

    if not clean:
        raise ValueError(
            "Refresh token must not be empty."
        )

    return hashlib.sha256(
        clean.encode(
            "utf-8"
        )
    ).hexdigest()
