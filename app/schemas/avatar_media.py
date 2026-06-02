from pydantic import BaseModel, Field
from typing import Dict, List, Optional


class AvatarFaceAnalysis(BaseModel):
    has_face: bool
    has_frontal_face: bool
    has_clear_lighting: bool
    emotional_presence_score: float
    identity_consistency_score: float
    quality_score: float
    recommended_for_avatar: bool


class AvatarMediaUploadResponse(BaseModel):
    asset_id: str
    profile_id: str
    asset_type: str
    title: str
    content_type: str
    size_bytes: int
    signed_url: str
    expires_in_seconds: int
    face_analysis: Optional[Dict] = None


class AvatarMediaMetadata(BaseModel):
    asset_id: str
    profile_id: str
    asset_type: str
    title: str
    filename: str
    content_type: str
    size_bytes: int
    storage_path: str
    created_at: str


class AvatarMediaSignRequest(BaseModel):
    asset_id: str
    expires_in_seconds: Optional[int] = 900


class AvatarMediaSignResponse(BaseModel):
    asset_id: str
    signed_url: str
    expires_in_seconds: int


class AvatarMediaListResponse(BaseModel):
    profile_id: str
    assets: List[AvatarMediaMetadata] = Field(default_factory=list)
