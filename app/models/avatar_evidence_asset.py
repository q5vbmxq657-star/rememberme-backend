from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID


@dataclass(frozen=True)
class AvatarEvidenceAsset:
    asset_id: UUID
    profile_id: UUID

    asset_type: str
    evidence_kind: str

    title: str
    filename: str
    content_type: str
    size_bytes: int

    storage_backend: str
    storage_key: str
    storage_path: Optional[str]

    processing_status: str
    selection_status: str

    is_included_in_avatar: bool
    included_in_avatar_at: Optional[datetime]
    archived_at: Optional[datetime]

    duration_seconds: Optional[float]

    quality_score: float

    has_face: bool
    has_frontal_face: bool
    has_clear_lighting: bool
    has_voice: bool

    voice_usable: bool
    motion_usable: bool

    emotional_presence_score: float
    identity_consistency_score: float

    motion_quality_score: float
    expression_range_score: float
    lip_visibility_score: float
    head_pose_stability_score: float

    recommended_for_avatar: bool

    rejection_code: Optional[str]
    rejection_reason: Optional[str]

    analysis_version: str

    analysis_metadata: Dict[str, Any] = field(
        default_factory=dict
    )

    source_metadata: Dict[str, Any] = field(
        default_factory=dict
    )

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_archived(self) -> bool:
        return (
            self.archived_at is not None
            or self.processing_status == "archived"
        )

    @property
    def is_training_source(self) -> bool:
        return self.evidence_kind in {
            "identity_photo",
            "motion_video",
            "voice_sample",
        }

    @property
    def is_quality_eligible(self) -> bool:
        return (
            self.quality_score >= 0.72
            and self.rejection_reason is None
        )

    @property
    def is_active_avatar_evidence(self) -> bool:
        return (
            self.is_training_source
            and not self.is_archived
            and self.is_included_in_avatar
            and self.is_quality_eligible
            and self.processing_status
            in {
                "ready",
                "training",
            }
        )

    @property
    def deterministic_priority(self) -> tuple:
        return (
            1 if self.selection_status == "primary" else 0,
            1 if self.is_included_in_avatar else 0,
            1 if self.recommended_for_avatar else 0,
            self.quality_score,
            self.updated_at
            or datetime.min.replace(
                tzinfo=timezone.utc
            ),
            str(self.asset_id),
        )
