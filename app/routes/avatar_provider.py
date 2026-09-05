import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.schemas.avatar_provider import (
    AvatarProviderSubmitRequest,
    AvatarProviderSubmitResponse,
    AvatarProviderStatusResponse,
)
from app.services.avatar_provider_service import (
    AvatarProviderStatusUnavailableError,
    avatar_provider_service,
)
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileNotFoundError,
    DigitalHumanProfileRepositoryError,
)
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)

router = APIRouter(prefix="/v1/avatar-provider", tags=["avatar-provider"])


@router.post("/submit", response_model=AvatarProviderSubmitResponse)
async def submit_avatar_provider_job(
    request: AvatarProviderSubmitRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarProviderSubmitResponse:
    require_profile_access(
        principal=principal,
        profile_id=request.profile_id,
    )
    state = await avatar_provider_service.submit(
        provider=request.provider,
        profile_id=request.profile_id,
        package_record_id=request.package_record_id,
        package=request.package,
    )

    return AvatarProviderSubmitResponse(
        external_job_id=state.external_job_id,
        external_avatar_id=state.external_avatar_id,
        status=state.status,
        preview_url=state.preview_url,
        error_message=state.error_message,
    )


@router.get("/status/{external_job_id}", response_model=AvatarProviderStatusResponse)
async def get_avatar_provider_job_status(
    external_job_id: str,
    profile_id: UUID = Query(...),
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarProviderStatusResponse:
    await asyncio.to_thread(
        require_profile_access,
        principal=principal,
        profile_id=profile_id,
    )

    try:
        job_profile_id = await asyncio.to_thread(
            avatar_provider_service.require_training_job_profile_id,
            external_job_id,
        )
    except DigitalHumanProfileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider job was not found.",
        ) from error
    except DigitalHumanProfileRepositoryError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provider job persistence is unavailable.",
        ) from error

    if job_profile_id != profile_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider job was not found.",
        )

    try:
        state = await avatar_provider_service.status(external_job_id)
    except AvatarProviderStatusUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Avatar training status is temporarily unavailable. Please try again shortly.",
            headers={"Retry-After": "12"},
        ) from error

    return AvatarProviderStatusResponse(
        external_job_id=state.external_job_id,
        external_avatar_id=state.external_avatar_id,
        status=state.status,
        preview_url=state.preview_url,
        error_message=state.error_message,
    )
