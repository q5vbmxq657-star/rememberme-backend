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
    ):
        self.arguments = (user_id, profile_id, consent_verified)

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
