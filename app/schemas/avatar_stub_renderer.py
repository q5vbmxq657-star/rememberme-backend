from pydantic import BaseModel
from typing import Optional


class AvatarStubRenderRequest(BaseModel):
    job_id: str
    renderer_provider: str = "internal_preview_renderer"


class AvatarStubRenderResponse(BaseModel):
    job_id: str
    profile_id: str
    render_status: str
    output_asset_id: str
    output_type: str
    preview_video_url: str
    message: str


class AvatarStubRenderStatusResponse(BaseModel):
    job_id: str
    render_status: str
    output_asset_id: Optional[str] = None
    preview_video_url: Optional[str] = None
    message: str
