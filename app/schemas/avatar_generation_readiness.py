from pydantic import BaseModel, Field
from typing import List, Optional


class AvatarGenerationReadinessRequest(BaseModel):
    profile_id: str
    consent_verified: bool = True
    voice_identity_score: float = 0.0
    behavioral_persona_score: float = 0.0


class AvatarCapabilityDecision(BaseModel):
    capability: str
    status: str
    score: float
    reason: str


class AvatarGenerationReadinessResponse(BaseModel):
    profile_id: str
    generation_status: str
    recommended_avatar_mode: str
    overall_generation_score: float
    identity_score: float
    motion_score: float
    voice_score: float
    persona_score: float
    consent_score: float
    primary_identity_asset_id: Optional[str]
    primary_motion_asset_id: Optional[str]
    capabilities: List[AvatarCapabilityDecision] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    next_best_actions: List[str] = Field(default_factory=list)
