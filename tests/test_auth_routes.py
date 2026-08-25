from __future__ import annotations

import asyncio
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import (
    HTTPException,
    Response,
)

from app.routes import auth as auth_routes
from app.schemas.auth import (
    AppleSessionExchangeRequest,
    SessionRefreshRequest,
)
from app.security.session_tokens import (
    SessionTokenPair,
)
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    UserAuthenticationError,
)
from app.models.user_identity import (
    UserIdentity,
    UserStatus,
)
from app.services.user_session_repository import (
    InvalidRefreshCredentialError,
)


NOW = datetime.now(
    timezone.utc
)


def user() -> UserIdentity:
    return UserIdentity(
        user_id=uuid4(),
        status=UserStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )


def pair() -> SessionTokenPair:
    return SessionTokenPair(
        access_token="stay-access",
        refresh_token="stay-refresh",
        access_expires_at=(
            NOW
            + timedelta(
                minutes=15
            )
        ),
        refresh_expires_at=(
            NOW
            + timedelta(
                days=30
            )
        ),
    )


class FakeBootstrapService:
    def __init__(
        self,
        *,
        resolved_user=None,
        error=None,
    ):
        self.resolved_user = (
            resolved_user
        )

        self.error = error

    async def authenticate(
        self,
        *,
        identity_token,
        authorization_code,
        nonce,
    ):
        if self.error is not None:
            raise self.error

        return self.resolved_user


class FakeSessionRepository:
    def __init__(
        self,
        *,
        created=None,
        rotated=None,
        revoked=True,
        rotate_error=None,
    ):
        self.created = created
        self.rotated = rotated
        self.revoked = revoked
        self.rotate_error = (
            rotate_error
        )

        self.created_user_id = None
        self.rotated_token = None
        self.revoked_identity = None

    def create_session(
        self,
        *,
        user_id,
    ):
        self.created_user_id = user_id
        return self.created

    def rotate_refresh_token(
        self,
        *,
        refresh_token,
    ):
        self.rotated_token = (
            refresh_token
        )

        if self.rotate_error:
            raise self.rotate_error

        return self.rotated

    def revoke_session(
        self,
        *,
        session_id,
        user_id,
    ):
        self.revoked_identity = (
            session_id,
            user_id,
        )

        return self.revoked


def test_apple_exchange_creates_durable_stay_session(
    monkeypatch,
):
    resolved_user = user()
    token_pair = pair()

    repository = FakeSessionRepository(
        created=(
            SimpleNamespace(
                user_id=(
                    resolved_user.user_id
                )
            ),
            token_pair,
        )
    )

    monkeypatch.setattr(
        auth_routes,
        "get_apple_bootstrap_authentication_service",
        lambda: FakeBootstrapService(
            resolved_user=(
                resolved_user
            )
        ),
    )

    monkeypatch.setattr(
        auth_routes,
        "get_user_session_repository",
        lambda: repository,
    )

    response = Response()

    result = asyncio.run(
        auth_routes.exchange_apple_identity(
            AppleSessionExchangeRequest(
                identity_token=(
                    "verified-apple-token"
                ),
                authorization_code=(
                    "single-use-code"
                ),
                nonce="test-raw-nonce-with-sufficient-entropy",
            ),
            response,
        )
    )

    assert (
        repository.created_user_id
        == resolved_user.user_id
    )

    assert (
        result.access_token
        == token_pair.access_token
    )

    assert (
        result.refresh_token
        == token_pair.refresh_token
    )

    assert (
        result.user_id
        == resolved_user.user_id
    )

    assert (
        response.headers[
            "Cache-Control"
        ]
        == "no-store"
    )

    assert (
        response.headers[
            "Pragma"
        ]
        == "no-cache"
    )


def test_apple_exchange_invalid_identity_fails_without_session(
    monkeypatch,
):
    repository = (
        FakeSessionRepository()
    )

    monkeypatch.setattr(
        auth_routes,
        "get_apple_bootstrap_authentication_service",
        lambda: FakeBootstrapService(
            error=UserAuthenticationError(
                "Authentication failed."
            )
        ),
    )

    monkeypatch.setattr(
        auth_routes,
        "get_user_session_repository",
        lambda: repository,
    )

    with pytest.raises(
        HTTPException
    ) as captured:
        asyncio.run(
            auth_routes.exchange_apple_identity(
                AppleSessionExchangeRequest(
                    identity_token="invalid",
                    authorization_code=(
                        "single-use-code"
                    ),
                    nonce="test-raw-nonce-with-sufficient-entropy",
                ),
                Response(),
            )
        )

    assert (
        captured.value.status_code
        == 401
    )

    assert (
        repository.created_user_id
        is None
    )


def test_refresh_rotates_through_canonical_repository(
    monkeypatch,
):
    resolved_user = user()
    token_pair = pair()

    repository = (
        FakeSessionRepository(
            rotated=(
                SimpleNamespace(
                    user_id=(
                        resolved_user.user_id
                    )
                ),
                token_pair,
            )
        )
    )

    monkeypatch.setattr(
        auth_routes,
        "get_user_session_repository",
        lambda: repository,
    )

    response = Response()

    result = (
        auth_routes.refresh_session(
            SessionRefreshRequest(
                refresh_token=(
                    "old-refresh"
                )
            ),
            response,
        )
    )

    assert (
        repository.rotated_token
        == "old-refresh"
    )

    assert (
        result.user_id
        == resolved_user.user_id
    )

    assert (
        result.refresh_token
        == "stay-refresh"
    )

    assert (
        response.headers[
            "Cache-Control"
        ]
        == "no-store"
    )


def test_refresh_replay_failure_is_sanitized(
    monkeypatch,
):
    repository = (
        FakeSessionRepository(
            rotate_error=(
                InvalidRefreshCredentialError(
                    "private database detail"
                )
            )
        )
    )

    monkeypatch.setattr(
        auth_routes,
        "get_user_session_repository",
        lambda: repository,
    )

    with pytest.raises(
        HTTPException
    ) as captured:
        auth_routes.refresh_session(
            SessionRefreshRequest(
                refresh_token="old"
            ),
            Response(),
        )

    assert (
        captured.value.status_code
        == 401
    )

    assert (
        "private database detail"
        not in str(
            captured.value.detail
        )
    )


def test_logout_revokes_exact_current_session(
    monkeypatch,
):
    resolved_user = user()
    session_id = uuid4()

    repository = (
        FakeSessionRepository(
            revoked=True
        )
    )

    monkeypatch.setattr(
        auth_routes,
        "get_user_session_repository",
        lambda: repository,
    )

    principal = (
        AuthenticatedSessionPrincipal(
            user=resolved_user,
            session_id=session_id,
            access_expires_at=(
                NOW
                + timedelta(
                    minutes=5
                )
            ),
        )
    )

    response = (
        auth_routes.logout_session(
            principal
        )
    )

    assert response.status_code == 204

    assert (
        repository.revoked_identity
        == (
            session_id,
            resolved_user.user_id,
        )
    )


def test_logout_missing_session_fails_closed(
    monkeypatch,
):
    resolved_user = user()

    repository = (
        FakeSessionRepository(
            revoked=False
        )
    )

    monkeypatch.setattr(
        auth_routes,
        "get_user_session_repository",
        lambda: repository,
    )

    principal = (
        AuthenticatedSessionPrincipal(
            user=resolved_user,
            session_id=uuid4(),
            access_expires_at=(
                NOW
                + timedelta(
                    minutes=5
                )
            ),
        )
    )

    with pytest.raises(
        HTTPException
    ) as captured:
        auth_routes.logout_session(
            principal
        )

    assert (
        captured.value.status_code
        == 401
    )


def test_auth_schema_requires_authorization_code_exchange():
    fields = (
        AppleSessionExchangeRequest
        .model_fields
    )

    assert "identity_token" in fields
    assert "nonce" in fields

    assert "authorization_code" in fields


def test_main_wires_public_bootstrap_without_mobile_shared_secret():
    from pathlib import Path

    root = (
        Path(__file__)
        .resolve()
        .parents[1]
    )

    source = (
        root
        / "app/main.py"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        source.count(
            "router as auth_router"
        )
        == 1
    )

    assert (
        source.count(
            "auth_router,"
        )
        == 1
    )

    assert (
        'prefix="/v1/auth"'
        in source
    )

    auth_block_start = (
        source.index(
            "app.include_router(\n"
            "    auth_router,"
        )
    )

    auth_block_end = source.index(
        "app.include_router(",
        auth_block_start + 1,
    )

    auth_block = source[
        auth_block_start:
        auth_block_end
    ]

    assert "Depends(require_client_key)" not in auth_block
    assert "dependencies=authenticated" not in auth_block

    assert (
        "authenticated = [Depends(require_authenticated_principal)]"
        in source
    )
