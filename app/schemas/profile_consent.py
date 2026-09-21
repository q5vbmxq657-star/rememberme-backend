from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


CONSENT_POLICY_VERSION = "avatar-consent-v1"
Purpose = Literal["photo_likeness", "video_motion", "voice_synthesis", "memory_context", "provider_processing"]


class PurposeConsentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, le=9223372036854775806)
    policy_version: Literal["avatar-consent-v1"]
    purposes: list[Purpose] = Field(max_length=5)
    understands_ai_disclosure: bool
    understands_revocation: bool

    @model_validator(mode="after")
    def validate_grant(self):
        if len(set(self.purposes)) != len(self.purposes):
            raise ValueError("Duplicate purposes are not allowed.")
        if self.purposes and not (self.understands_ai_disclosure and self.understands_revocation):
            raise ValueError("An explicit acknowledgement is required.")
        if set(self.purposes) - {"provider_processing"} and "provider_processing" not in self.purposes:
            raise ValueError("Provider processing permission is required.")
        return self


class PurposeConsentSnapshot(BaseModel):
    profile_id: UUID
    revision: int
    policy_version: str
    purposes: list[Purpose]
