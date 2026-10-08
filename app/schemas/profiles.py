from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileProvisionRequest(StrictProfileModel):
    profile_id: UUID
    consent_verified: bool = False
    display_name: str | None = Field(default=None, max_length=200)
    relationship: str | None = Field(default=None, max_length=200)


class ProfileProvisionResponse(StrictProfileModel):
    profile_id: UUID
    role: Literal["owner"] = "owner"
    status: Literal["active"] = "active"
    created: bool
