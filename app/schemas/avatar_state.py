# P31.12_CANONICAL_UNIFIED_AVATAR_STATE
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


AvatarStateStatus = Literal[
    "ready",
    "in_progress",
    "blocked",
    "missing",
    "unknown",
]

AvatarOverallStatus = Literal[
    "ready_for_runtime",
    "needs_training",
    "blocked",
    "unknown",
]


class AvatarStateDimension(BaseModel):
    status: AvatarStateStatus
    score: float = Field(ge=0.0, le=1.0)
    label: str
    reason: str
    evidence: List[str] = Field(default_factory=list)
    next_action: Optional[str] = None


class AvatarUnifiedStateResponse(BaseModel):
    profile_id: str
    overall_status: AvatarOverallStatus
    readiness_score: float = Field(ge=0.0, le=1.0)
    can_start_runtime: bool
    next_best_action: str

    face_video: AvatarStateDimension
    voice: AvatarStateDimension
    memory: AvatarStateDimension
    behavior: AvatarStateDimension
    consent: AvatarStateDimension
    runtime: AvatarStateDimension
    identity_verification: AvatarStateDimension

    source_integrity: Dict[str, Any] = Field(default_factory=dict)
