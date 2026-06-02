from fastapi import APIRouter

from app.schemas.avatar_video import TavusVideoCreateRequest, TavusVideoCreateResponse
from app.services.avatar_provider_service import avatar_provider_service

router = APIRouter(prefix="/v1/avatar-video", tags=["avatar-video"])


@router.post("/tavus/create", response_model=TavusVideoCreateResponse)
async def create_tavus_video(
    request: TavusVideoCreateRequest,
) -> TavusVideoCreateResponse:
    state = await avatar_provider_service.create_tavus_video(
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
) -> TavusVideoCreateResponse:
    state = await avatar_provider_service.fetch_tavus_video_status(
        external_job_id=external_job_id,
    )

    return TavusVideoCreateResponse(
        external_job_id=state.external_job_id,
        external_avatar_id=state.external_avatar_id,
        status=state.status,
        preview_url=state.preview_url,
        error_message=state.error_message,
    )
