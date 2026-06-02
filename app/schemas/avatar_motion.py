from pydantic import BaseModel, Field
from typing import List, Optional


class AvatarMotionReadinessRequest(BaseModel):
    profile_id: str


class AvatarMotionAssetAnalysis(BaseModel):
    asset_id: str
    title: str
    asset_type: str
    content_type: str
    size_bytes: int
    estimated_duration_seconds: float
    motion_quality_score: float
    expression_range_score: float
    lip_visibility_score: float
    head_pose_stability_score: float
    talking_portrait_suitability_score: float
    recommended_for_motion_learning: bool
    motion_role: str


class AvatarMotionReadinessResponse(BaseModel):
    profile_id: str
    motion_status: str
    motion_identity_score: float
    expression_learning_score: float
    talking_portrait_readiness_score: float
    usable_motion_asset_count: int
    recommended_primary_motion_asset_id: Optional[str]
    motion_assets: List[AvatarMotionAssetAnalysis] = Field(default_factory=list)
    missing_requirements: List[str] = Field(default_factory=list)
    next_best_actions: List[str] = Field(default_factory=list)
