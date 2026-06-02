from pydantic import BaseModel, Field
from typing import List, Optional


class AvatarTrainingAsset(BaseModel):
    id: str
    type: str
    title: str
    duration_seconds: Optional[float] = None
    quality_score: Optional[float] = None
    has_face: Optional[bool] = None
    has_voice: Optional[bool] = None
    has_clear_lighting: Optional[bool] = None
    has_frontal_face: Optional[bool] = None
    emotional_tags: List[str] = Field(default_factory=list)


class AvatarTrainingReadinessRequest(BaseModel):
    profile_id: str
    profile_name: str
    consent_verified: bool = False
    assets: List[AvatarTrainingAsset] = Field(default_factory=list)
    memory_count: int = 0
    persona_confidence_score: float = 0.0


class AvatarTrainingGap(BaseModel):
    area: str
    severity: str
    recommendation: str


class AvatarTrainingReadinessResponse(BaseModel):
    profile_id: str
    visual_identity_score: float
    voice_identity_score: float
    behavioral_persona_score: float
    consent_safety_score: float
    overall_readiness_score: float
    readiness_level: str
    gaps: List[AvatarTrainingGap]
    next_best_actions: List[str]
