from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AvatarRuntimeProvider(str, Enum):
    TAVUS = "tavus"


class AvatarRuntimeTransport(str, Enum):
    LIVEKIT = "livekit"


class AvatarRuntimeSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    display_name: str
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
    created_at: datetime
    expires_at: Optional[datetime] = None
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
    latency_ms: Optional[int] = Field(default=None, ge=0)
    created_at: datetime


class AvatarRuntimeOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: str
    updated_at: datetime
