from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class ProfileMembershipRole(str, Enum):
    OWNER = "owner"


class ProfileMembershipStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    REVOKED = "revoked"


@dataclass(frozen=True)
class ProfileMembership:
    membership_id: UUID
    user_id: UUID
    profile_id: UUID
    role: ProfileMembershipRole
    status: ProfileMembershipStatus
    created_at: datetime
    updated_at: datetime

    @property
    def permits_access(self) -> bool:
        return (
            self.role is ProfileMembershipRole.OWNER
            and self.status
            is ProfileMembershipStatus.ACTIVE
        )
