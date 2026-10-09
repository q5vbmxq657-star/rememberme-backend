import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.profile_membership import (
    ProfileMembership,
    ProfileMembershipRole,
    ProfileMembershipStatus,
)
from app.models.user_identity import UserIdentity, UserStatus
from app.routes import profiles as profile_routes
from app.schemas.profiles import ProfileProvisionRequest
from app.security.user_auth import AuthenticatedSessionPrincipal
from app.services.profile_membership_repository import (
    ProfileMembershipRepositoryError,
    ProfileProvisioningConflictError,
)


NOW = datetime.now(timezone.utc)


def principal() -> AuthenticatedSessionPrincipal:
    return AuthenticatedSessionPrincipal(
        user=UserIdentity(
            user_id=uuid4(),
            status=UserStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        ),
        session_id=uuid4(),
        access_expires_at=NOW + timedelta(minutes=15),
    )


class FakeRepository:
    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error
        self.arguments = None

    def provision_owned_profile(
        self,
        *,
        user_id,
        profile_id,
        consent_verified,
        **identity,
    ):
        self.arguments = (user_id, profile_id, consent_verified)
        self.identity = identity

        if self.error is not None:
            raise self.error

        return self.result


def test_profile_provisioning_binds_authenticated_owner(monkeypatch):
    authenticated = principal()
    profile_id = uuid4()
    membership = ProfileMembership(
        membership_id=uuid4(),
        user_id=authenticated.user.user_id,
        profile_id=profile_id,
        role=ProfileMembershipRole.OWNER,
        status=ProfileMembershipStatus.ACTIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    repository = FakeRepository(result=(membership, True))

    monkeypatch.setattr(
        profile_routes,
        "ProfileMembershipRepository",
        lambda: repository,
    )

    response = asyncio.run(
        profile_routes.provision_profile(
            ProfileProvisionRequest(
                profile_id=profile_id,
                consent_verified=True,
            ),
            authenticated,
        )
    )

    assert repository.arguments == (
        authenticated.user.user_id,
        profile_id,
        True,
    )
    assert response.profile_id == profile_id
    assert response.role == "owner"
    assert response.status == "active"
    assert response.created is True


@pytest.mark.parametrize(
    ("repository_error", "expected_status"),
    [
        (ProfileProvisioningConflictError("conflict"), 409),
        (ProfileMembershipRepositoryError("offline"), 503),
    ],
)
def test_profile_provisioning_failures_are_truthful(
    monkeypatch,
    repository_error,
    expected_status,
):
    repository = FakeRepository(error=repository_error)
    monkeypatch.setattr(
        profile_routes,
        "ProfileMembershipRepository",
        lambda: repository,
    )

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            profile_routes.provision_profile(
                ProfileProvisionRequest(
                    profile_id=uuid4(),
                    consent_verified=False,
                ),
                principal(),
            )
        )

    assert captured.value.status_code == expected_status


def test_directory_is_scoped_to_authenticated_session_and_not_cached(monkeypatch):
    import json
    authenticated = principal()
    class Directory:
        def account_directory(self, *, user_id, session_id):
            assert user_id == authenticated.user.user_id
            assert session_id == authenticated.session_id
            return []
    monkeypatch.setattr(profile_routes, "ProfileMembershipRepository", Directory)
    response = asyncio.run(profile_routes.account_profiles(authenticated))
    assert json.loads(response.body) == {"profiles": []}
    assert response.headers["cache-control"] == "no-store"


def test_directory_failure_is_not_reported_as_a_new_account(monkeypatch):
    class Directory:
        def account_directory(self, **kwargs):
            raise ProfileMembershipRepositoryError("offline")
    monkeypatch.setattr(profile_routes, "ProfileMembershipRepository", Directory)
    with pytest.raises(HTTPException) as error:
        asyncio.run(profile_routes.account_profiles(principal()))
    assert error.value.status_code == 503


def test_identity_replay_does_not_write_placeholder_names():
    from app.services.profile_membership_repository import ProfileMembershipRepository
    class Cursor:
        def execute(self, *args):
            pytest.fail("An unknown or fallback identity must never be persisted")
    for name in (None, "", "  ", "Recovered memory space"):
        ProfileMembershipRepository._save_missing_identity(Cursor(), uuid4(), name, "")


def test_identity_replay_only_fills_missing_metadata():
    from app.services.profile_membership_repository import ProfileMembershipRepository
    statements = []
    class Cursor:
        def execute(self, query, params):
            statements.append((query, params))
    identifier = uuid4()
    ProfileMembershipRepository._save_missing_identity(Cursor(), identifier, " Anna ", "Mother")
    query, params = statements[0]
    assert "NULLIF(BTRIM(metadata->>'display_name'), '') IS NULL" in query
    assert params[1] == identifier
    assert '"display_name": "Anna"' in params[0]
