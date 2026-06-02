from pydantic import BaseModel, Field
from typing import List


class AvatarRuntimePlanRequest(BaseModel):
    profile_id: str
    blueprint_status: str
    realism_mode: str
    voice_strategy: str
    visual_strategy: str
    behavior_strategy: str
    lip_sync_strategy: str
    safety_constraints: List[str] = Field(default_factory=list)


class AvatarRuntimePlanResponse(BaseModel):
    profile_id: str
    runtime_status: str
    session_mode: str
    visual_renderer: str
    voice_renderer: str
    lip_sync_runtime: str
    behavior_conditioning: str
    latency_target_ms: int
    fallback_mode: str
    disabled_capabilities: List[str]
    required_client_features: List[str]
