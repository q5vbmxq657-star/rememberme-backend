from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class PodcastInvitationStatus(str, Enum):
    pending = "pending"
    recording = "recording"
    uploaded = "uploaded"
    processing = "processing"
    completed = "completed"
    retryable_failed = "retryable_failed"
    expired = "expired"


class PodcastInvitationCreateRequest(BaseModel):
    profile_id: UUID
    requester_name: str = Field(min_length=1, max_length=80)
    subject_name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=8, max_length=600)
    locale: str = Field(default="de-DE", min_length=2, max_length=20)


class PodcastInvitationCreateResponse(BaseModel):
    invitation_id: UUID
    profile_id: UUID
    share_url: str
    status: PodcastInvitationStatus
    expires_at: datetime


class PodcastPublicMetadata(BaseModel):
    invitation_id: UUID
    requester_name: str
    subject_name: str
    prompt: str
    prompt_audio_url: str | None = None
    status: PodcastInvitationStatus
    expires_at: datetime


class PodcastUploadResponse(BaseModel):
    invitation_id: UUID
    status: PodcastInvitationStatus
    memory_id: UUID
    message: str


class PodcastInvitationRecord(BaseModel):
    invitation_id: UUID
    profile_id: UUID
    created_by_user_id: UUID
    requester_name: str
    subject_name: str
    prompt: str
    prompt_audio_asset_id: UUID | None
    response_audio_asset_id: UUID | None
    memory_id: UUID
    status: PodcastInvitationStatus
    transcript: str | None
    summary: str | None
    memory_payload: dict[str, Any] | None
    safe_error_code: str | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class PodcastMemoryImport(BaseModel):
    invitation_id: UUID
    memory_id: UUID
    profile_id: UUID
    title: str
    original_text: str
    summary: str
    avatar_memory_text: str
    emotional_tags: list[str] = Field(default_factory=list)
    extracted_topics: list[str] = Field(default_factory=list)
    confidence_score: float
    audio_url: str
    audio_content_type: str
    created_at: datetime


class PodcastMemoryImportList(BaseModel):
    memories: list[PodcastMemoryImport] = Field(default_factory=list)


class PodcastMemoryImportAcknowledgement(BaseModel):
    invitation_id: UUID
    acknowledged: bool
