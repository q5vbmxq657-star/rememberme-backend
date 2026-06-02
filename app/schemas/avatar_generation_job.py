from pydantic import BaseModel
from typing import Optional


class AvatarGenerationJobRequest(BaseModel):
    profile_id: str
    generation_mode: str
    identity_asset_id: Optional[str] = None
    motion_asset_id: Optional[str] = None
    voice_enabled: bool = False
    persona_enabled: bool = False


class AvatarGenerationJobResponse(BaseModel):
    job_id: str
    profile_id: str
    status: str
    progress: float
    current_stage: str
    generation_mode: str
    identity_asset_id: Optional[str] = None
    motion_asset_id: Optional[str] = None
    preview_video_url: Optional[str] = None
