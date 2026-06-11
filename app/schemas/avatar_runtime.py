from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# Existing runtime-plan contract
# =============================================================================


class AvatarRuntimePlanRequest(BaseModel):
    profile_id: str
    blueprint_status: str
    realism_mode: str
    voice_strategy: str
    visual_strategy: str
    behavior_strategy: str
    lip_sync_strategy: str
    safety_constraints: List[str] = Field(default_factory=list)


class AvatarRuntimePlanResponse(BaseModel):
    profile_id: str
    runtime_status: str
    session_mode: str
    visual_renderer: str
    voice_renderer: str
    lip_sync_runtime: str
    behavior_conditioning: str
    latency_target_ms: int
    fallback_mode: str
    disabled_capabilities: List[str]
    required_client_features: List[str]


# =============================================================================
# Productive session-runtime contract used by the iOS gateway
# =============================================================================


class AvatarRuntimeProvider(str, Enum):
    BEYOND_PRESENCE = "beyond_presence"
    HEYGEN_LIVE_AVATAR = "heygen_liveavatar"
    SIMLI = "simli"
    TAVUS = "tavus"
    LOCAL = "local"


class AvatarRuntimeTransport(str, Enum):
    LIVEKIT = "livekit"
    WEBRTC = "webrtc"
    REMOTE_VIDEO = "remote_video"
    LOCAL_VIDEO = "local_video"
    LOCAL_AVATAR = "local_avatar"


class AvatarRuntimeSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    display_name: str
    preferred_providers: List[AvatarRuntimeProvider]
    preferred_transport: AvatarRuntimeTransport
    fallback_enabled: bool
    allow_tavus_fallback: bool
    allow_local_fallback: bool
    requires_custom_identity: bool
    requires_external_voice_audio: bool
    maximum_accepted_latency_ms: int = Field(ge=0, le=120_000)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("display_name must not be empty")

        if len(cleaned) > 160:
            raise ValueError("display_name is too long")

        return cleaned

    @field_validator("preferred_providers")
    @classmethod
    def validate_preferred_providers(
        cls,
        value: List[AvatarRuntimeProvider],
    ) -> List[AvatarRuntimeProvider]:
        result: List[AvatarRuntimeProvider] = []

        for provider in value:
            if provider not in result:
                result.append(provider)

        if not result:
            raise ValueError("preferred_providers must not be empty")

        return result


class AvatarLiveKitSessionDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    server_url: str
    token: str
    room_name: Optional[str] = None
    avatar_participant_identity: Optional[str] = None
    expires_at: Optional[datetime] = None


class AvatarRuntimeSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    profile_id: UUID
    provider: AvatarRuntimeProvider
    transport: AvatarRuntimeTransport
    provider_avatar_id: Optional[str] = None
    livekit: Optional[AvatarLiveKitSessionDescriptor] = None
    preview_video_url: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    fallback_providers: List[AvatarRuntimeProvider] = Field(default_factory=list)
    metadata: Dict[str, str] = Field(default_factory=dict)


class AvatarRuntimeSpeechMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    session_id: str
    profile_id: UUID
    text: str
    voice_synthesis_mode: str
    voice_provider: Optional[str] = None
    runtime_voice_id: Optional[str] = None
    allow_provider_fallback: bool = True
    created_at: datetime

    @field_validator("session_id", "text", "voice_synthesis_mode")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("required string must not be empty")

        return cleaned

    @field_validator("text")
    @classmethod
    def validate_speech_text_length(cls, value: str) -> str:
        if len(value) > 20_000:
            raise ValueError("speech text exceeds maximum length")

        return value


class AvatarRuntimeSpeechResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    session_id: str
    resolved_provider: AvatarRuntimeProvider
    transport: AvatarRuntimeTransport
    livekit: Optional[AvatarLiveKitSessionDescriptor] = None
    video_url: Optional[str] = None
    fallback_used: bool
    latency_ms: Optional[int] = Field(default=None, ge=0)
    created_at: datetime


class AvatarRuntimeOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: str
    updated_at: datetime
