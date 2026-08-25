from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class UserStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class ExternalIdentityProvider(str, Enum):
    APPLE = "apple"


@dataclass(frozen=True)
class UserIdentity:
    user_id: UUID
    status: UserStatus
    created_at: datetime
    updated_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE


@dataclass(frozen=True)
class ExternalUserIdentity:
    identity_id: UUID
    user_id: UUID
    provider: ExternalIdentityProvider
    provider_subject: str
    email: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.provider_subject.strip():
            raise ValueError(
                "provider_subject must not be empty."
            )
