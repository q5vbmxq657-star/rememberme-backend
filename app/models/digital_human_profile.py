from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID


@dataclass(frozen=True)
class DigitalHumanProfile:
    profile_id: UUID

    quality_tier: str
    quality_percentage: int

    avatar_provider: Optional[str]
    avatar_replica_id: Optional[str]
    avatar_persona_id: Optional[str]
    avatar_training_job_id: Optional[str]
    avatar_training_status: str

    voice_provider: Optional[str]
    voice_id: Optional[str]
    voice_training_job_id: Optional[str]
    voice_training_status: str

    approved_portrait_url: Optional[str]

    consent_verified: bool
    training_version: int

    runtime_verified_at: Optional[datetime]
    avatar_ready_at: Optional[datetime]
    voice_ready_at: Optional[datetime]

    last_error_code: Optional[str]
    last_error_message: Optional[str]

    identity_verification_status: str = "not_evaluated"
    current_identity_verification_receipt_id: Optional[UUID] = None
    identity_verified_at: Optional[datetime] = None

    metadata: Dict[str, Any] = field(default_factory=dict)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def has_verified_identity(self) -> bool:
        return (
            self.identity_verification_status == "verified"
            and self.current_identity_verification_receipt_id is not None
            and self.identity_verified_at is not None
        )

    @property
    def has_runtime_avatar(self) -> bool:
        return (
            self.consent_verified
            and self.avatar_training_status == "ready"
            and bool(self.avatar_provider)
            and bool(self.avatar_replica_id)
        )

    @property
    def has_personalized_voice(self) -> bool:
        return (
            self.consent_verified
            and self.voice_training_status == "ready"
            and bool(self.voice_provider)
            and bool(self.voice_id)
        )
