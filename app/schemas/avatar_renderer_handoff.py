from pydantic import BaseModel, Field
from typing import List, Optional


class AvatarRendererHandoffRequest(BaseModel):
    job_id: str
    renderer_provider: str = "internal_preview_renderer"
    expires_in_seconds: int = 900


class AvatarRendererInputAsset(BaseModel):
    asset_id: str
    role: str
    signed_url: str
    content_type: str
    expires_in_seconds: int


class AvatarRendererOutputContract(BaseModel):
    expected_type: str
    format: str
    storage_policy: str
    requires_signed_delivery: bool


class AvatarRendererHandoffResponse(BaseModel):
    job_id: str
    profile_id: str
    renderer_provider: str
    avatar_mode: str
    renderer: str
    voice_strategy: str
    lip_sync_strategy: str
    input_assets: List[AvatarRendererInputAsset] = Field(default_factory=list)
    safety_constraints: List[str] = Field(default_factory=list)
    output_contract: AvatarRendererOutputContract
    handoff_status: str
    message: str
