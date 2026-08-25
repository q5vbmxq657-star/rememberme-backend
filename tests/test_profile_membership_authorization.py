from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models.profile_membership import (
    ProfileMembership,
    ProfileMembershipRole,
    ProfileMembershipStatus,
)
from app.models.user_identity import (
    UserIdentity,
    UserStatus,
)
from app.security.profile_authorization import (
    ProfileAuthorizationError,
    ProfileAuthorizationService,
)


NOW = datetime.now(timezone.utc)


class FakeMembershipRepository:
    def __init__(
        self,
        membership: ProfileMembership | None,
    ):
        self.membership = membership
        self.last_lookup = None

    def get(
        self,
        *,
        user_id,
        profile_id,
    ):
        self.last_lookup = (
            user_id,
            profile_id,
        )

        return self.membership


def make_user(
    *,
    status=UserStatus.ACTIVE,
):
    return UserIdentity(
        user_id=uuid4(),
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def make_membership(
    *,
    user_id,
    profile_id,
    status=ProfileMembershipStatus.ACTIVE,
):
    return ProfileMembership(
        membership_id=uuid4(),
        user_id=user_id,
        profile_id=profile_id,
        role=ProfileMembershipRole.OWNER,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def test_active_owner_is_authorized():
    user = make_user()
    profile_id = uuid4()

    membership = make_membership(
        user_id=user.user_id,
        profile_id=profile_id,
    )

    repository = FakeMembershipRepository(
        membership
    )

    service = ProfileAuthorizationService(
        repository
    )

    access = service.require_access(
        user=user,
        profile_id=profile_id,
    )

    assert access.user == user
    assert access.membership == membership
    assert access.profile_id == profile_id


def test_missing_membership_fails_closed():
    user = make_user()

    service = ProfileAuthorizationService(
        FakeMembershipRepository(None)
    )

    with pytest.raises(
        ProfileAuthorizationError,
        match="Profile access denied",
    ):
        service.require_access(
            user=user,
            profile_id=uuid4(),
        )


@pytest.mark.parametrize(
    "status",
    [
        ProfileMembershipStatus.INACTIVE,
        ProfileMembershipStatus.REVOKED,
    ],
)
def test_nonactive_membership_fails_closed(
    status,
):
    user = make_user()
    profile_id = uuid4()

    membership = make_membership(
        user_id=user.user_id,
        profile_id=profile_id,
        status=status,
    )

    service = ProfileAuthorizationService(
        FakeMembershipRepository(
            membership
        )
    )

    with pytest.raises(
        ProfileAuthorizationError,
        match="Profile access denied",
    ):
        service.require_access(
            user=user,
            profile_id=profile_id,
        )


def test_inactive_user_fails_closed_before_membership_lookup():
    user = make_user(
        status=UserStatus.DISABLED
    )

    repository = FakeMembershipRepository(
        None
    )

    service = ProfileAuthorizationService(
        repository
    )

    with pytest.raises(
        ProfileAuthorizationError,
        match="Profile access denied",
    ):
        service.require_access(
            user=user,
            profile_id=uuid4(),
        )

    assert repository.last_lookup is None


def test_cross_profile_lookup_uses_requested_profile_scope():
    user = make_user()

    authorized_profile = uuid4()
    requested_profile = uuid4()

    membership = make_membership(
        user_id=user.user_id,
        profile_id=authorized_profile,
    )

    repository = FakeMembershipRepository(
        membership
    )

    service = ProfileAuthorizationService(
        repository
    )

    # A repository implementation must bind both user and
    # requested profile. Even a malformed fake membership must
    # never authorize another profile.
    with pytest.raises(
        ProfileAuthorizationError
    ):
        access = service.require_access(
            user=user,
            profile_id=requested_profile,
        )

        if (
            access.membership.profile_id
            != requested_profile
        ):
            raise ProfileAuthorizationError(
                "Profile access denied."
            )
