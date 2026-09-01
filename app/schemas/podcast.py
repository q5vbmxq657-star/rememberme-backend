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
    theme: str = Field(default="life_story", min_length=3, max_length=40)
    prompt: str | None = Field(default=None, min_length=8, max_length=600)
    locale: str = Field(default="de-DE", min_length=2, max_length=20)


class PodcastInvitationCreateResponse(BaseModel):
    invitation_id: UUID
    profile_id: UUID
    share_url: str
    status: PodcastInvitationStatus
    expires_at: datetime


class PodcastInvitationSummary(BaseModel):
    invitation_id: UUID
    profile_id: UUID
    subject_name: str
    theme: str
    status: PodcastInvitationStatus
    answer_count: int = 0
    created_at: datetime
    completed_at: datetime | None = None


class PodcastInvitationSummaryList(BaseModel):
    invitations: list[PodcastInvitationSummary] = Field(default_factory=list)


class PodcastPublicPrompt(BaseModel):
    prompt_id: str
    category: str
    question: str
    audio_url: str | None = None


class PodcastPublicMetadata(BaseModel):
    invitation_id: UUID
    requester_name: str
    subject_name: str
    prompt: str
    prompt_audio_url: str | None = None
    theme: str = "life_story"
    prompts: list[PodcastPublicPrompt] = Field(default_factory=list)
    status: PodcastInvitationStatus
    expires_at: datetime


class PodcastUploadResponse(BaseModel):
    invitation_id: UUID
    status: PodcastInvitationStatus
    memory_id: UUID
    memory_ids: list[UUID] = Field(default_factory=list)
    message: str


class PodcastInvitationRecord(BaseModel):
    invitation_id: UUID
    profile_id: UUID
    created_by_user_id: UUID
    requester_name: str
    subject_name: str
    prompt: str
    theme: str = "life_story"
    prompt_sequence: list[dict[str, Any]] = Field(default_factory=list)
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
    speaker_confirmed_subject: bool = False
    voice_training_consent_granted: bool = False
    voice_training_consented_at: datetime | None = None
    voice_training_used_at: datetime | None = None


class PodcastResponseRecord(BaseModel):
    response_id: UUID
    invitation_id: UUID
    turn_index: int
    prompt_id: str
    category: str
    question: str
    audio_asset_id: UUID
    memory_id: UUID
    transcript: str
    summary: str
    memory_payload: dict[str, Any]
    created_at: datetime


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
    category: str = "personal"
    prompt: str = ""
    voice_training_eligible: bool = False
    voice_training_used_at: datetime | None = None


class PodcastMemoryImportList(BaseModel):
    memories: list[PodcastMemoryImport] = Field(default_factory=list)


class PodcastMemoryImportAcknowledgement(BaseModel):
    invitation_id: UUID
    acknowledged: bool
