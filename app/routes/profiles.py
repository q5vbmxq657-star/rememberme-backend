from __future__ import annotations

from uuid import UUID
import psycopg

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from app.security.profile_authorization import require_profile_access
from app.schemas.profile_consent import PurposeConsentSnapshot, PurposeConsentUpdate
from app.services.profile_consent_repository import ProfileConsentRepository, ConsentAccessDenied, ConsentRevisionConflict

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


@router.get("/{profile_id}/consent", response_model=PurposeConsentSnapshot)
def read_profile_consent(profile_id: UUID, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    require_profile_access(principal=principal, profile_id=profile_id)
    try:
        result = ProfileConsentRepository().read(profile_id)
        require_profile_access(principal=principal, profile_id=profile_id)
        return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "no-store"})
    except psycopg.Error as error:
        raise HTTPException(503, "Permissions are temporarily unavailable.") from error


@router.put("/{profile_id}/consent", response_model=PurposeConsentSnapshot)
def update_profile_consent(profile_id: UUID, payload: PurposeConsentUpdate,
                           principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    require_profile_access(principal=principal, profile_id=profile_id)
    try:
        result = ProfileConsentRepository().update(profile_id=profile_id,
            user_id=principal.user.user_id, session_id=principal.session_id, update=payload)
        require_profile_access(principal=principal, profile_id=profile_id)
        return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "no-store"})
    except ConsentAccessDenied as error:
        raise HTTPException(404, "Profile not found.") from error
    except ConsentRevisionConflict as error:
        raise HTTPException(409, "Permissions changed. Refresh before trying again.") from error
    except psycopg.Error as error:
        raise HTTPException(503, "Permissions could not be saved. Please try again.") from error


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
