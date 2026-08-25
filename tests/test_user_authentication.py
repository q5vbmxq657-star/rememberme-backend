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

from app.models.user_identity import (
    UserIdentity,
    UserStatus,
)
from app.security.apple_identity import (
    AppleIdentityVerificationError,
)
from app.security.session_tokens import (
    SessionTokenVerificationError,
    VerifiedSessionAccess,
)
from app.security.user_auth import (
    SessionAuthenticationService,
    UserAuthenticationError,
    UserAuthenticationService,
)


NOW = datetime.now(
    timezone.utc
)


def active_user(
    *,
    user_id=None,
) -> UserIdentity:
    resolved = user_id or uuid4()

    return UserIdentity(
        user_id=resolved,
        status=UserStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeAppleVerifier:
    def __init__(
        self,
        *,
        verified=None,
        error=None,
    ):
        self.verified = verified
        self.error = error
        self.calls = []

    async def exchange_and_verify(
        self,
        credential,
        *,
        authorization_code,
        nonce,
    ):
        self.calls.append(
            (
                credential,
                authorization_code,
                nonce,
            )
        )

        if self.error is not None:
            raise self.error

        return self.verified


class FakeCredentialCipher:
    def __init__(self):
        self.calls = []

    def encrypt(self, token):
        self.calls.append(token)
        return b"encrypted-apple-refresh-token"


class FakeBootstrapRepository:
    def __init__(
        self,
        *,
        user,
        identity=None,
    ):
        self.user = user

        self.identity = (
            identity
            or SimpleNamespace(
                user_id=user.user_id
            )
        )

        self.calls = []

    def resolve_or_create_external_identity(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        return (
            self.user,
            self.identity,
            False,
        )


class FakeTokenAuthority:
    def __init__(
        self,
        *,
        verified=None,
        error=None,
    ):
        self.verified = verified
        self.error = error
        self.calls = []

    def verify_access(
        self,
        token,
    ):
        self.calls.append(
            token
        )

        if self.error is not None:
            raise self.error

        return self.verified


class FakeSessionRepository:
    def __init__(
        self,
        *,
        session=None,
    ):
        self.session = session
        self.calls = []

    def get_active_session(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        return self.session


class FakeUserRepository:
    def __init__(
        self,
        *,
        user=None,
    ):
        self.user = user
        self.calls = []

    def get_user(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        return self.user


def test_apple_bootstrap_resolves_canonical_user():
    user = active_user()

    verifier = FakeAppleVerifier(
        verified=SimpleNamespace(
            subject="apple-subject",
            email="person@example.com",
            refresh_token="apple-refresh-token",
        )
    )

    repository = (
        FakeBootstrapRepository(
            user=user
        )
    )

    service = UserAuthenticationService(
        verifier=verifier,
        credential_cipher=(
            FakeCredentialCipher()
        ),
        repository=repository,
    )

    result = asyncio.run(
        service.authenticate(
            identity_token="apple-token",
            authorization_code=(
                "single-use-code"
            ),
            nonce="test-raw-nonce-with-sufficient-entropy",
        )
    )

    assert result == user

    assert verifier.calls == [
        (
            "apple-token",
            "single-use-code",
            "test-raw-nonce-with-sufficient-entropy",
        )
    ]

    assert len(
        repository.calls
    ) == 1


def test_unverified_apple_token_never_reaches_repository():
    verifier = FakeAppleVerifier(
        error=(
            AppleIdentityVerificationError(
                "private parser detail"
            )
        )
    )

    repository = (
        FakeBootstrapRepository(
            user=active_user()
        )
    )

    service = UserAuthenticationService(
        verifier=verifier,
        credential_cipher=(
            FakeCredentialCipher()
        ),
        repository=repository,
    )

    with pytest.raises(
        UserAuthenticationError,
        match="Authentication failed",
    ):
        asyncio.run(
            service.authenticate(
                identity_token=(
                    "raw-secret-token"
                ),
                authorization_code=(
                    "single-use-code"
                ),
                nonce="test-raw-nonce-with-sufficient-entropy",
            )
        )

    assert repository.calls == []


def test_empty_apple_bootstrap_credential_fails_before_verifier():
    verifier = FakeAppleVerifier(
        verified=None
    )

    repository = (
        FakeBootstrapRepository(
            user=active_user()
        )
    )

    service = UserAuthenticationService(
        verifier=verifier,
        credential_cipher=(
            FakeCredentialCipher()
        ),
        repository=repository,
    )

    with pytest.raises(
        UserAuthenticationError
    ):
        asyncio.run(
            service.authenticate(
                identity_token="   ",
                authorization_code=(
                    "single-use-code"
                ),
                nonce="test-raw-nonce-with-sufficient-entropy",
            )
        )

    assert verifier.calls == []
    assert repository.calls == []


def test_session_access_resolves_active_user_and_session():
    user = active_user()
    session_id = uuid4()

    verified = VerifiedSessionAccess(
        user_id=user.user_id,
        session_id=session_id,
        expires_at=(
            NOW
            + timedelta(
                minutes=10
            )
        ),
    )

    session = SimpleNamespace(
        session_id=session_id,
        user_id=user.user_id,
        is_revoked=False,
    )

    token_authority = (
        FakeTokenAuthority(
            verified=verified
        )
    )

    session_repository = (
        FakeSessionRepository(
            session=session
        )
    )

    user_repository = (
        FakeUserRepository(
            user=user
        )
    )

    service = (
        SessionAuthenticationService(
            token_authority=(
                token_authority
            ),
            session_repository=(
                session_repository
            ),
            user_repository=(
                user_repository
            ),
        )
    )

    principal = service.authenticate(
        access_token="stay-access-token"
    )

    assert principal.user == user

    assert (
        principal.session_id
        == session_id
    )

    assert token_authority.calls == [
        "stay-access-token"
    ]

    assert (
        session_repository.calls
        == [
            {
                "session_id": session_id,
                "user_id": user.user_id,
            }
        ]
    )

    assert (
        user_repository.calls
        == [
            {
                "user_id": user.user_id,
            }
        ]
    )


def test_invalid_stay_access_token_never_reaches_database():
    token_authority = (
        FakeTokenAuthority(
            error=(
                SessionTokenVerificationError(
                    "jwt implementation detail"
                )
            )
        )
    )

    session_repository = (
        FakeSessionRepository()
    )

    user_repository = (
        FakeUserRepository()
    )

    service = (
        SessionAuthenticationService(
            token_authority=(
                token_authority
            ),
            session_repository=(
                session_repository
            ),
            user_repository=(
                user_repository
            ),
        )
    )

    with pytest.raises(
        UserAuthenticationError,
        match="Authentication failed",
    ):
        service.authenticate(
            access_token=(
                "raw-stay-access-secret"
            )
        )

    assert (
        session_repository.calls
        == []
    )

    assert (
        user_repository.calls
        == []
    )


def test_revoked_or_missing_session_fails_before_user_lookup():
    user_id = uuid4()

    verified = VerifiedSessionAccess(
        user_id=user_id,
        session_id=uuid4(),
        expires_at=(
            NOW
            + timedelta(
                minutes=5
            )
        ),
    )

    user_repository = (
        FakeUserRepository(
            user=active_user(
                user_id=user_id
            )
        )
    )

    service = (
        SessionAuthenticationService(
            token_authority=(
                FakeTokenAuthority(
                    verified=verified
                )
            ),
            session_repository=(
                FakeSessionRepository(
                    session=None
                )
            ),
            user_repository=(
                user_repository
            ),
        )
    )

    with pytest.raises(
        UserAuthenticationError
    ):
        service.authenticate(
            access_token="access"
        )

    assert user_repository.calls == []


def test_session_user_binding_mismatch_fails_closed():
    user_id = uuid4()
    session_id = uuid4()

    verified = VerifiedSessionAccess(
        user_id=user_id,
        session_id=session_id,
        expires_at=(
            NOW
            + timedelta(
                minutes=5
            )
        ),
    )

    mismatched_session = (
        SimpleNamespace(
            session_id=session_id,
            user_id=uuid4(),
            is_revoked=False,
        )
    )

    user_repository = (
        FakeUserRepository(
            user=active_user(
                user_id=user_id
            )
        )
    )

    service = (
        SessionAuthenticationService(
            token_authority=(
                FakeTokenAuthority(
                    verified=verified
                )
            ),
            session_repository=(
                FakeSessionRepository(
                    session=(
                        mismatched_session
                    )
                )
            ),
            user_repository=(
                user_repository
            ),
        )
    )

    with pytest.raises(
        UserAuthenticationError
    ):
        service.authenticate(
            access_token="access"
        )

    assert user_repository.calls == []


def test_disabled_user_fails_closed():
    user_id = uuid4()
    session_id = uuid4()

    disabled = UserIdentity(
        user_id=user_id,
        status=UserStatus.DISABLED,
        created_at=NOW,
        updated_at=NOW,
    )

    verified = VerifiedSessionAccess(
        user_id=user_id,
        session_id=session_id,
        expires_at=(
            NOW
            + timedelta(
                minutes=5
            )
        ),
    )

    session = SimpleNamespace(
        session_id=session_id,
        user_id=user_id,
        is_revoked=False,
    )

    service = (
        SessionAuthenticationService(
            token_authority=(
                FakeTokenAuthority(
                    verified=verified
                )
            ),
            session_repository=(
                FakeSessionRepository(
                    session=session
                )
            ),
            user_repository=(
                FakeUserRepository(
                    user=disabled
                )
            ),
        )
    )

    with pytest.raises(
        UserAuthenticationError
    ):
        service.authenticate(
            access_token="access"
        )


def test_raw_access_token_never_appears_in_public_error():
    raw_token = (
        "raw-super-secret-access-token"
    )

    service = (
        SessionAuthenticationService(
            token_authority=(
                FakeTokenAuthority(
                    error=(
                        SessionTokenVerificationError(
                            raw_token
                        )
                    )
                )
            ),
            session_repository=(
                FakeSessionRepository()
            ),
            user_repository=(
                FakeUserRepository()
            ),
        )
    )

    with pytest.raises(
        UserAuthenticationError
    ) as captured:
        service.authenticate(
            access_token=raw_token
        )

    assert (
        raw_token
        not in str(
            captured.value
        )
    )


def test_empty_stay_access_credential_fails_before_authority():
    token_authority = (
        FakeTokenAuthority()
    )

    session_repository = (
        FakeSessionRepository()
    )

    user_repository = (
        FakeUserRepository()
    )

    service = (
        SessionAuthenticationService(
            token_authority=(
                token_authority
            ),
            session_repository=(
                session_repository
            ),
            user_repository=(
                user_repository
            ),
        )
    )

    with pytest.raises(
        UserAuthenticationError
    ):
        service.authenticate(
            access_token="   "
        )

    assert token_authority.calls == []
    assert session_repository.calls == []
    assert user_repository.calls == []
