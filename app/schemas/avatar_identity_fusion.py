from pydantic import BaseModel, Field
from typing import List, Optional


class AvatarIdentityFusionRequest(BaseModel):
    profile_id: str
    min_quality_score: float = 0.70


class AvatarIdentityReferenceAsset(BaseModel):
    asset_id: str
    title: str
    asset_type: str
    content_type: str
    size_bytes: int
    quality_score: float
    has_face: bool
    has_frontal_face: bool
    has_clear_lighting: bool
    emotional_presence_score: float
    identity_consistency_score: float
    recommended_for_avatar: bool
    reference_role: str


class AvatarIdentityFusionResponse(BaseModel):
    profile_id: str
    fusion_status: str
    primary_reference_asset_id: Optional[str]
    visual_identity_score: float
    identity_stability_score: float
    usable_reference_count: int
    rejected_reference_count: int
    reference_pack: List[AvatarIdentityReferenceAsset] = Field(default_factory=list)
    rejected_assets: List[AvatarIdentityReferenceAsset] = Field(default_factory=list)
    next_best_actions: List[str] = Field(default_factory=list)
