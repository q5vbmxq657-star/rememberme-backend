from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class TavusVideoCreateRequest(BaseModel):
    profile_id: UUID
    replica_id: str
    script: str

class TavusVideoCreateResponse(BaseModel):
    external_job_id: str
    external_avatar_id: Optional[str] = None
    status: str
    preview_url: Optional[str] = None
    error_message: Optional[str] = None
