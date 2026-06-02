from pydantic import BaseModel, Field
from typing import List, Optional


class AvatarIdentityBlueprintRequest(BaseModel):
    profile_id: str
    profile_name: str
    readiness_level: str
    visual_identity_score: float
    voice_identity_score: float
    behavioral_persona_score: float
    consent_safety_score: float
    memories: List[str] = Field(default_factory=list)
    persona_traits: List[str] = Field(default_factory=list)
    speaking_style: List[str] = Field(default_factory=list)
    identity_anchors: List[str] = Field(default_factory=list)


class AvatarIdentityBlueprintResponse(BaseModel):
    profile_id: str
    blueprint_status: str
    realism_mode: str
    voice_strategy: str
    visual_strategy: str
    behavior_strategy: str
    lip_sync_strategy: str
    safety_constraints: List[str]
    missing_requirements: List[str]
    next_pipeline_step: str
