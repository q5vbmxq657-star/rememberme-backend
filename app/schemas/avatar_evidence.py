from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


AvatarEvidenceKind = Literal[
    "identity_photo",
    "motion_video",
    "voice_sample",
    "generated_preview",
    "trained_replica",
]

AvatarEvidenceProcessingStatus = Literal[
    "uploaded",
    "analyzing",
    "ready",
    "rejected",
    "training",
    "failed",
    "archived",
]

AvatarEvidenceSelectionStatus = Literal[
    "not_selected",
    "selected",
    "primary",
    "removed",
]


class AvatarEvidenceAssetResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    asset_id: UUID
    profile_id: UUID

    asset_type: str
    evidence_kind: AvatarEvidenceKind

    title: str
    filename: str
    content_type: str
    size_bytes: int

    processing_status: (
        AvatarEvidenceProcessingStatus
    )

    selection_status: (
        AvatarEvidenceSelectionStatus
    )

    is_included_in_avatar: bool
    included_in_avatar_at: Optional[datetime]
    archived_at: Optional[datetime]

    duration_seconds: Optional[float]

    quality_score: float = Field(
        ge=0.0,
        le=1.0,
    )

    has_face: bool
    has_frontal_face: bool
    has_clear_lighting: bool
    has_voice: bool

    voice_usable: bool
    motion_usable: bool

    emotional_presence_score: float = Field(
        ge=0.0,
        le=1.0,
    )

    identity_consistency_score: float = Field(
        ge=0.0,
        le=1.0,
    )

    motion_quality_score: float = Field(
        ge=0.0,
        le=1.0,
    )

    expression_range_score: float = Field(
        ge=0.0,
        le=1.0,
    )

    lip_visibility_score: float = Field(
        ge=0.0,
        le=1.0,
    )

    head_pose_stability_score: float = Field(
        ge=0.0,
        le=1.0,
    )

    recommended_for_avatar: bool

    rejection_code: Optional[str]
    rejection_reason: Optional[str]

    analysis_version: str
    analysis_metadata: Dict[str, Any]
    source_metadata: Dict[str, Any]

    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class AvatarEvidenceListResponse(BaseModel):
    profile_id: UUID

    assets: List[
        AvatarEvidenceAssetResponse
    ] = Field(default_factory=list)

    primary_identity_asset_id: Optional[UUID]
    primary_motion_asset_id: Optional[UUID]
    primary_voice_asset_id: Optional[UUID]


class AvatarEvidenceSelectionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    profile_id: UUID
    make_primary: bool = False


class AvatarEvidenceMutationResponse(BaseModel):
    status: Literal[
        "selected",
        "removed",
        "archived",
    ]

    asset: AvatarEvidenceAssetResponse
