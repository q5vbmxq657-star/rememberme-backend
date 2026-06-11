from fastapi import APIRouter, HTTPException, Request

from app.schemas.avatar_stub_renderer import (
    AvatarStubRenderRequest,
    AvatarStubRenderResponse,
    AvatarStubRenderStatusResponse,
)
from app.services.avatar_stub_renderer_service import AvatarStubRendererService

router = APIRouter()


@router.post("/render", response_model=AvatarStubRenderResponse)
def render_avatar_stub(
    request: Request,
    body: AvatarStubRenderRequest
):
    try:
        service = AvatarStubRendererService()
        base_url = str(request.base_url).rstrip("/")

        return service.render(
            request=body,
            base_url=base_url
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar preview rendering failed: {str(error)}"
        )


@router.get("/jobs/{job_id}", response_model=AvatarStubRenderStatusResponse)
def get_avatar_stub_render_status(
    request: Request,
    job_id: str
):
    try:
        service = AvatarStubRendererService()
        base_url = str(request.base_url).rstrip("/")

        return service.get_render_status(
            job_id=job_id,
            base_url=base_url
        )
    except Exception as error:
        raise HTTPException(
            status_code=404,
            detail=f"Avatar preview render status failed: {str(error)}"
        )


@router.get("/outputs/{output_asset_id}")
def get_avatar_stub_output(output_asset_id: str):
    try:
        service = AvatarStubRendererService()
        return service.read_output(output_asset_id)
    except Exception as error:
        raise HTTPException(
            status_code=404,
            detail=f"Avatar preview output failed: {str(error)}"
        )
