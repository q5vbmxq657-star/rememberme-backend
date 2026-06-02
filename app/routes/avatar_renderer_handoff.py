from fastapi import APIRouter, HTTPException, Request

from app.schemas.avatar_renderer_handoff import (
    AvatarRendererHandoffRequest,
    AvatarRendererHandoffResponse,
)
from app.services.avatar_renderer_handoff_service import AvatarRendererHandoffService

router = APIRouter()


@router.post("/build", response_model=AvatarRendererHandoffResponse)
def build_avatar_renderer_handoff(
    request: Request,
    body: AvatarRendererHandoffRequest
):
    try:
        service = AvatarRendererHandoffService()
        base_url = str(request.base_url).rstrip("/")

        return service.build_handoff(
            request=body,
            base_url=base_url
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar renderer handoff failed: {str(error)}"
        )
