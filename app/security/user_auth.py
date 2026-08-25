from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from uuid import UUID

from fastapi import (
    Depends,
    HTTPException,
    status,
)
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from starlette.concurrency import (
    run_in_threadpool,
)

from app.models.user_identity import (
    ExternalIdentityProvider,
    UserIdentity,
)
from app.security.apple_identity import (
    AppleIdentityConfigurationError,
    AppleIdentityVerificationError,
    AppleIdentityVerifier,
    AppleRefreshTokenCipher,
)
from app.security.session_tokens import (
    SessionTokenAuthority,
    SessionTokenConfigurationError,
    SessionTokenVerificationError,
)
from app.services.user_identity_repository import (
    UserIdentityRepository,
    UserIdentityRepositoryError,
)
from app.services.user_session_repository import (
    UserSessionRepository,
    UserSessionRepositoryError,
)


class UserAuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthenticatedSessionPrincipal:
    user: UserIdentity
    session_id: UUID
    access_expires_at: datetime


_bearer_scheme = HTTPBearer(
    auto_error=False
)


class UserAuthenticationService:
    """
    Apple bootstrap identity authentication.

    This service is used only to establish a STAY session.
    Apple identity tokens are not normal STAY API bearer tokens.
    """

    def __init__(
        self,
        *,
        verifier: AppleIdentityVerifier,
        credential_cipher: AppleRefreshTokenCipher,
        repository: UserIdentityRepository,
    ) -> None:
        self.verifier = verifier
        self.credential_cipher = (
            credential_cipher
        )
        self.repository = repository

    async def authenticate(
        self,
        *,
        identity_token: str,
        authorization_code: str,
        nonce: str,
    ) -> UserIdentity:
        credential = identity_token.strip()
        code = authorization_code.strip()
        raw_nonce = nonce.strip()

        if (
            not credential
            or not code
            or not raw_nonce
        ):
            raise UserAuthenticationError(
                "Authentication failed."
            )

        try:
            verified = (
                await self.verifier
                .exchange_and_verify(
                    credential,
                    authorization_code=code,
                    nonce=raw_nonce,
                )
            )

            encrypted_refresh_token = (
                self.credential_cipher.encrypt(
                    verified.refresh_token
                )
            )

        except AppleIdentityVerificationError as error:
            raise UserAuthenticationError(
                "Authentication failed."
            ) from error

        try:
            user, identity, _ = await run_in_threadpool(
                self.repository
                .resolve_or_create_external_identity,
                provider=ExternalIdentityProvider.APPLE,
                provider_subject=verified.subject,
                email=verified.email,
                provider_refresh_token_ciphertext=(
                    encrypted_refresh_token
                ),
                provider_credentials_verified_at=(
                    datetime.now(
                        timezone.utc
                    )
                ),
            )

        except (
            UserIdentityRepositoryError,
            ValueError,
        ):
            raise

        if (
            identity.user_id != user.user_id
            or not user.is_active
        ):
            raise UserAuthenticationError(
                "Authentication failed."
            )

        return user


class SessionAuthenticationService:
    """
    Canonical normal-API STAY session authentication.

    A cryptographically valid access token is insufficient by itself.
    The referenced server-side session must still be active and the
    referenced canonical user must still exist and remain active.
    """

    def __init__(
        self,
        *,
        token_authority: SessionTokenAuthority,
        session_repository: UserSessionRepository,
        user_repository: UserIdentityRepository,
    ) -> None:
        self.token_authority = token_authority
        self.session_repository = session_repository
        self.user_repository = user_repository

    def authenticate(
        self,
        *,
        access_token: str,
    ) -> AuthenticatedSessionPrincipal:
        credential = access_token.strip()

        if not credential:
            raise UserAuthenticationError(
                "Authentication failed."
            )

        try:
            verified = (
                self.token_authority
                .verify_access(
                    credential
                )
            )

        except (
            SessionTokenVerificationError,
            ValueError,
        ) as error:
            raise UserAuthenticationError(
                "Authentication failed."
            ) from error

        session = (
            self.session_repository
            .get_active_session(
                session_id=verified.session_id,
                user_id=verified.user_id,
            )
        )

        if session is None:
            raise UserAuthenticationError(
                "Authentication failed."
            )

        if (
            session.session_id
            != verified.session_id
            or session.user_id
            != verified.user_id
            or session.is_revoked
        ):
            raise UserAuthenticationError(
                "Authentication failed."
            )

        user = (
            self.user_repository
            .get_user(
                user_id=verified.user_id
            )
        )

        if (
            user is None
            or user.user_id
            != verified.user_id
            or not user.is_active
        ):
            raise UserAuthenticationError(
                "Authentication failed."
            )

        return AuthenticatedSessionPrincipal(
            user=user,
            session_id=verified.session_id,
            access_expires_at=(
                verified.expires_at
            ),
        )


@lru_cache(maxsize=1)
def get_apple_identity_verifier(
) -> AppleIdentityVerifier:
    return AppleIdentityVerifier()


@lru_cache(maxsize=1)
def get_user_identity_repository(
) -> UserIdentityRepository:
    return UserIdentityRepository()


@lru_cache(maxsize=1)
def get_session_token_authority(
) -> SessionTokenAuthority:
    return SessionTokenAuthority()


@lru_cache(maxsize=1)
def get_user_session_repository(
) -> UserSessionRepository:
    return UserSessionRepository(
        token_authority=(
            get_session_token_authority()
        )
    )


def get_apple_bootstrap_authentication_service(
) -> UserAuthenticationService:
    return UserAuthenticationService(
        verifier=get_apple_identity_verifier(),
        credential_cipher=(
            AppleRefreshTokenCipher()
        ),
        repository=(
            get_user_identity_repository()
        ),
    )


def get_session_authentication_service(
) -> SessionAuthenticationService:
    return SessionAuthenticationService(
        token_authority=(
            get_session_token_authority()
        ),
        session_repository=(
            get_user_session_repository()
        ),
        user_repository=(
            get_user_identity_repository()
        ),
    )


def _authentication_required(
) -> HTTPException:
    return HTTPException(
        status_code=(
            status.HTTP_401_UNAUTHORIZED
        ),
        detail="Authentication required.",
        headers={
            "WWW-Authenticate": "Bearer"
        },
    )


def _authentication_failed(
) -> HTTPException:
    return HTTPException(
        status_code=(
            status.HTTP_401_UNAUTHORIZED
        ),
        detail="Authentication failed.",
        headers={
            "WWW-Authenticate": "Bearer"
        },
    )


def _authentication_unavailable(
) -> HTTPException:
    return HTTPException(
        status_code=(
            status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        detail=(
            "Authentication service "
            "is unavailable."
        ),
    )


def require_authenticated_principal(
    credentials: (
        HTTPAuthorizationCredentials | None
    ) = Depends(_bearer_scheme),
) -> AuthenticatedSessionPrincipal:
    if (
        credentials is None
        or credentials.scheme.lower()
        != "bearer"
        or not credentials.credentials.strip()
    ):
        raise _authentication_required()

    try:
        service = (
            get_session_authentication_service()
        )

        return service.authenticate(
            access_token=(
                credentials.credentials
            ),
        )

    except (
        SessionTokenConfigurationError,
        UserSessionRepositoryError,
        UserIdentityRepositoryError,
    ) as error:
        raise _authentication_unavailable() from error

    except UserAuthenticationError as error:
        raise _authentication_failed() from error


def require_authenticated_user(
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> UserIdentity:
    return principal.user
