from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.schemas.avatar_video import TavusVideoCreateRequest, TavusVideoCreateResponse
from app.services.avatar_provider_service import avatar_provider_service
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)

router = APIRouter(prefix="/v1/avatar-video", tags=["avatar-video"])


@router.post("/tavus/create", response_model=TavusVideoCreateResponse)
async def create_tavus_video(
    request: TavusVideoCreateRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> TavusVideoCreateResponse:
    require_profile_access(
        principal=principal,
        profile_id=request.profile_id,
    )
    state = await avatar_provider_service.create_tavus_video(
        profile_id=request.profile_id,
        replica_id=request.replica_id,
        script=request.script,
    )

    return TavusVideoCreateResponse(
        external_job_id=state.external_job_id,
        external_avatar_id=state.external_avatar_id,
        status=state.status,
        preview_url=state.preview_url,
        error_message=state.error_message,
    )


@router.get("/tavus/status/{external_job_id}", response_model=TavusVideoCreateResponse)
async def get_tavus_video_status(
    external_job_id: str,
    profile_id: UUID = Query(...),
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> TavusVideoCreateResponse:
    require_profile_access(
        principal=principal,
        profile_id=profile_id,
    )
    state = await avatar_provider_service.fetch_tavus_video_status(
        external_job_id=external_job_id,
        expected_profile_id=profile_id,
    )

    return TavusVideoCreateResponse(
        external_job_id=state.external_job_id,
        external_avatar_id=state.external_avatar_id,
        status=state.status,
        preview_url=state.preview_url,
        error_message=state.error_message,
    )
