from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class UserSession:
    session_id: UUID
    user_id: UUID

    created_at: datetime
    updated_at: datetime

    access_expires_at: datetime
    refresh_expires_at: datetime

    revoked_at: datetime | None
    last_rotated_at: datetime

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
