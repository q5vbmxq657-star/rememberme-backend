from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class StrictProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileProvisionRequest(StrictProfileModel):
    profile_id: UUID
    consent_verified: bool = False


class ProfileProvisionResponse(StrictProfileModel):
    profile_id: UUID
    role: Literal["owner"] = "owner"
    status: Literal["active"] = "active"
    created: bool
