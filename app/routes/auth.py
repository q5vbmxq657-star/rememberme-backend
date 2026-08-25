from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)
from starlette.concurrency import (
    run_in_threadpool,
)

from app.schemas.auth import (
    AppleSessionExchangeRequest,
    SessionRefreshRequest,
    SessionTokenResponse,
)
from app.security.apple_identity import (
    AppleIdentityConfigurationError,
    AppleIdentityProviderUnavailableError,
)
from app.security.session_tokens import (
    SessionTokenConfigurationError,
    SessionTokenPair,
)
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    UserAuthenticationError,
    get_apple_bootstrap_authentication_service,
    get_user_session_repository,
    require_authenticated_principal,
)
from app.services.user_identity_repository import (
    UserIdentityRepositoryError,
)
from app.services.user_session_repository import (
    InvalidRefreshCredentialError,
    UserSessionRepositoryError,
)
from app.services.account_erasure_service import (
    AccountErasureService,
)


router = APIRouter()


def _authentication_failed(
) -> HTTPException:
    return HTTPException(
        status_code=(
            status.HTTP_401_UNAUTHORIZED
        ),
        detail="Authentication failed.",
    )


def _refresh_failed(
) -> HTTPException:
    return HTTPException(
        status_code=(
            status.HTTP_401_UNAUTHORIZED
        ),
        detail="Session refresh failed.",
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


def _disable_token_caching(
    response: Response,
) -> None:
    response.headers[
        "Cache-Control"
    ] = "no-store"

    response.headers[
        "Pragma"
    ] = "no-cache"


def _token_response(
    *,
    user_id,
    pair: SessionTokenPair,
) -> SessionTokenResponse:
    return SessionTokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        access_expires_at=(
            pair.access_expires_at
        ),
        refresh_expires_at=(
            pair.refresh_expires_at
        ),
        user_id=user_id,
    )


@router.post(
    "/apple/exchange",
    response_model=SessionTokenResponse,
    status_code=status.HTTP_200_OK,
)
async def exchange_apple_identity(
    payload: AppleSessionExchangeRequest,
    response: Response,
) -> SessionTokenResponse:
    """
    Establish a STAY session only after the Apple identity token,
    nonce, and single-use authorization code are verified with Apple.
    """

    _disable_token_caching(
        response
    )

    try:
        bootstrap_service = (
            get_apple_bootstrap_authentication_service()
        )

        user = (
            await bootstrap_service
            .authenticate(
                identity_token=(
                    payload.identity_token
                ),
                authorization_code=(
                    payload.authorization_code
                ),
                nonce=payload.nonce,
            )
        )

        session_repository = (
            get_user_session_repository()
        )

        _, pair = await run_in_threadpool(
            session_repository
            .create_session,
            user_id=user.user_id,
        )

    except UserAuthenticationError as error:
        raise _authentication_failed() from error

    except (
        AppleIdentityConfigurationError,
        SessionTokenConfigurationError,
        UserIdentityRepositoryError,
        UserSessionRepositoryError,
        AppleIdentityProviderUnavailableError,
    ) as error:
        raise _authentication_unavailable() from error

    return _token_response(
        user_id=user.user_id,
        pair=pair,
    )


@router.post(
    "/session/refresh",
    response_model=SessionTokenResponse,
    status_code=status.HTTP_200_OK,
)
def refresh_session(
    payload: SessionRefreshRequest,
    response: Response,
) -> SessionTokenResponse:
    _disable_token_caching(
        response
    )

    try:
        repository = (
            get_user_session_repository()
        )

        session, pair = (
            repository.rotate_refresh_token(
                refresh_token=(
                    payload.refresh_token
                )
            )
        )

    except (
        InvalidRefreshCredentialError,
        ValueError,
    ) as error:
        raise _refresh_failed() from error

    except (
        SessionTokenConfigurationError,
        UserSessionRepositoryError,
    ) as error:
        raise _authentication_unavailable() from error

    return _token_response(
        user_id=session.user_id,
        pair=pair,
    )


@router.post(
    "/session/logout",
    status_code=(
        status.HTTP_204_NO_CONTENT
    ),
    response_class=Response,
)
def logout_session(
    principal: (
        AuthenticatedSessionPrincipal
    ) = Depends(
        require_authenticated_principal
    ),
) -> Response:
    try:
        repository = (
            get_user_session_repository()
        )

        revoked = (
            repository.revoke_session(
                session_id=(
                    principal.session_id
                ),
                user_id=(
                    principal.user.user_id
                ),
            )
        )

    except (
        SessionTokenConfigurationError,
        UserSessionRepositoryError,
    ) as error:
        raise _authentication_unavailable() from error

    if not revoked:
        raise _authentication_failed()

    return Response(
        status_code=(
            status.HTTP_204_NO_CONTENT
        )
    )


@router.delete(
    "/account",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_account(
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> Response:
    """Delete the authenticated account and every profile-owned artifact."""
    try:
        await AccountErasureService().erase_account(
            user_id=principal.user.user_id
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account deletion could not be completed safely.",
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)
