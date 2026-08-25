from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class StrictAuthModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )


class AppleSessionExchangeRequest(
    StrictAuthModel
):
    identity_token: str = Field(
        min_length=1,
        max_length=16384,
    )

    authorization_code: str = Field(
        min_length=1,
        max_length=4096,
    )

    nonce: str = Field(
        min_length=32,
        max_length=512,
    )


class SessionRefreshRequest(
    StrictAuthModel
):
    refresh_token: str = Field(
        min_length=1,
        max_length=2048,
    )


class SessionTokenResponse(
    StrictAuthModel
):
    token_type: Literal["Bearer"] = (
        "Bearer"
    )

    access_token: str
    refresh_token: str

    access_expires_at: datetime
    refresh_expires_at: datetime

    user_id: UUID
