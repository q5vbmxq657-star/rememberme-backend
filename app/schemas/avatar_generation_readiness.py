from __future__ import annotations

from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


AvatarQualityTier = Literal[
    "signature_live",
    "expressive_live",
    "guided_live",
    "cinematic_portrait",
    "premium_presence",
    "blocked",
]

AvatarPresentationMode = Literal[
    "realtime_replica",
    "controlled_talking_avatar",
    "guided_talking_avatar",
    "cinematic_portrait",
    "abstract_presence",
    "blocked",
]

AvatarVoiceMode = Literal[
    "personalized",
    "personalized_when_ready",
    "warm_default",
    "silent",
]


class AvatarGenerationReadinessRequest(BaseModel):
    """
    Public readiness request.

    Only the profile identifier is authoritative input. Legacy fields sent by
    older clients are intentionally ignored so that rollout does not break
    already installed app builds.

    Consent, voice readiness, quality, avatar identity and persona evidence are
    resolved exclusively by the backend.
    """

    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
    )

    profile_id: UUID


class AvatarCapabilityDecision(BaseModel):
    capability: str
    status: Literal[
        "enabled",
        "degraded",
        "disabled",
    ]
    score: float = Field(
        ge=0.0,
        le=1.0,
    )
    reason: str


class AvatarPresentationSpec(BaseModel):
    quality_tier: AvatarQualityTier
    quality_percentage: int = Field(
        ge=0,
        le=100,
    )
    presentation_mode: AvatarPresentationMode
    voice_mode: AvatarVoiceMode

    framing: str
    camera_motion: str
    gesture_intensity: str
    facial_expression_intensity: str
    idle_motion: str

    show_full_face: bool = True
    show_upper_shoulders: bool = True
    allow_aggressive_close_up: bool = False

    user_title: str
    user_message: str


class AvatarGenerationReadinessResponse(BaseModel):
    profile_id: UUID

    generation_status: str
    recommended_avatar_mode: str

    quality_tier: AvatarQualityTier
    quality_percentage: int = Field(
        ge=0,
        le=100,
    )

    overall_generation_score: float = Field(
        ge=0.0,
        le=1.0,
    )
    identity_score: float = Field(
        ge=0.0,
        le=1.0,
    )
    motion_score: float = Field(
        ge=0.0,
        le=1.0,
    )
    voice_score: float = Field(
        ge=0.0,
        le=1.0,
    )
    persona_score: float = Field(
        ge=0.0,
        le=1.0,
    )
    consent_score: float = Field(
        ge=0.0,
        le=1.0,
    )

    primary_identity_asset_id: Optional[str]
    primary_motion_asset_id: Optional[str]

    presentation: AvatarPresentationSpec

    capabilities: List[
        AvatarCapabilityDecision
    ] = Field(default_factory=list)

    blockers: List[str] = Field(
        default_factory=list
    )

    next_best_actions: List[str] = Field(
        default_factory=list
    )
