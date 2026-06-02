from pydantic import BaseModel
from typing import Optional, Any, Dict

class AvatarProviderSubmitRequest(BaseModel):
    provider: str
    profile_id: str
    package_record_id: str
    package: Dict[str, Any]

class AvatarProviderSubmitResponse(BaseModel):
    external_job_id: str
    external_avatar_id: Optional[str] = None
    status: str
    preview_url: Optional[str] = None
    error_message: Optional[str] = None

class AvatarProviderStatusResponse(BaseModel):
    external_job_id: str
    external_avatar_id: Optional[str] = None
    status: str
    preview_url: Optional[str] = None
    error_message: Optional[str] = None
