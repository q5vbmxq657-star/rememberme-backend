from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.schemas.profiles import (
    ProfileProvisionRequest,
    ProfileProvisionResponse,
)
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)
from app.services.profile_membership_repository import (
    ProfileMembershipRepository,
    ProfileMembershipRepositoryError,
    ProfileProvisioningConflictError,
)


router = APIRouter()


@router.post(
    "",
    response_model=ProfileProvisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision_profile(
    payload: ProfileProvisionRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> ProfileProvisionResponse:
    try:
        membership, created = await run_in_threadpool(
            ProfileMembershipRepository().provision_owned_profile,
            user_id=principal.user.user_id,
            profile_id=payload.profile_id,
            consent_verified=payload.consent_verified,
        )

    except ProfileProvisioningConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Profile identity is already in use.",
        ) from error

    except ProfileMembershipRepositoryError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Profile provisioning is unavailable.",
        ) from error

    return ProfileProvisionResponse(
        profile_id=membership.profile_id,
        created=created,
    )
