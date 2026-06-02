from fastapi import APIRouter

from app.schemas.avatar_provider import (
    AvatarProviderSubmitRequest,
    AvatarProviderSubmitResponse,
    AvatarProviderStatusResponse,
)
from app.services.avatar_provider_service import avatar_provider_service

router = APIRouter(prefix="/v1/avatar-provider", tags=["avatar-provider"])


@router.post("/submit", response_model=AvatarProviderSubmitResponse)
async def submit_avatar_provider_job(
    request: AvatarProviderSubmitRequest,
) -> AvatarProviderSubmitResponse:
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
) -> AvatarProviderStatusResponse:
    state = await avatar_provider_service.status(external_job_id)

    return AvatarProviderStatusResponse(
        external_job_id=state.external_job_id,
        external_avatar_id=state.external_avatar_id,
        status=state.status,
        preview_url=state.preview_url,
        error_message=state.error_message,
    )
