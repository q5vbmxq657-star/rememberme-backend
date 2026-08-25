from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from fastapi import HTTPException, status

from app.models.profile_membership import (
    ProfileMembership,
)
from app.models.user_identity import (
    UserIdentity,
)
from app.services.profile_membership_repository import (
    ProfileMembershipRepository,
    ProfileMembershipRepositoryError,
)
from app.security.user_auth import AuthenticatedSessionPrincipal


class ProfileAuthorizationError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class AuthorizedProfileAccess:
    user: UserIdentity
    membership: ProfileMembership

    @property
    def profile_id(self) -> UUID:
        return self.membership.profile_id


class ProfileAuthorizationService:
    def __init__(
        self,
        repository: ProfileMembershipRepository,
    ) -> None:
        self.repository = repository

    def require_access(
        self,
        *,
        user: UserIdentity,
        profile_id: UUID,
    ) -> AuthorizedProfileAccess:
        if not user.is_active:
            raise ProfileAuthorizationError(
                "Profile access denied."
            )

        membership = self.repository.get(
            user_id=user.user_id,
            profile_id=profile_id,
        )

        if (
            membership is None
            or membership.user_id != user.user_id
            or membership.profile_id != profile_id
            or not membership.permits_access
        ):
            # Intentionally identical failure surface for
            # missing, cross-profile, revoked or inactive
            # membership. Do not leak profile existence.
            raise ProfileAuthorizationError(
                "Profile access denied."
            )

        return AuthorizedProfileAccess(
            user=user,
            membership=membership,
        )


@lru_cache(maxsize=1)
def get_profile_authorization_service(
) -> ProfileAuthorizationService:
    return ProfileAuthorizationService(
        ProfileMembershipRepository()
    )


def require_profile_access(
    *,
    principal: AuthenticatedSessionPrincipal,
    profile_id: UUID | str,
) -> AuthorizedProfileAccess:
    """Authorize one canonical profile without revealing its existence."""
    try:
        normalized_profile_id = (
            profile_id
            if isinstance(profile_id, UUID)
            else UUID(profile_id)
        )

        return get_profile_authorization_service().require_access(
            user=principal.user,
            profile_id=normalized_profile_id,
        )

    except (ValueError, ProfileAuthorizationError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found.",
        ) from error

    except ProfileMembershipRepositoryError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Profile authorization is unavailable.",
        ) from error
