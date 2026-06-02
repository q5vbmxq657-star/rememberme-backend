from pydantic import BaseModel
from typing import Optional

class TavusVideoCreateRequest(BaseModel):
    replica_id: str
    script: str

class TavusVideoCreateResponse(BaseModel):
    external_job_id: str
    external_avatar_id: Optional[str] = None
    status: str
    preview_url: Optional[str] = None
    error_message: Optional[str] = None
