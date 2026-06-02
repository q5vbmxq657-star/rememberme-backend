from fastapi import APIRouter, HTTPException

from app.schemas.avatar_identity_fusion import (
    AvatarIdentityFusionRequest,
    AvatarIdentityFusionResponse,
)
from app.services.avatar_identity_fusion_service import AvatarIdentityFusionService

router = APIRouter()


@router.post("/fuse", response_model=AvatarIdentityFusionResponse)
def fuse_avatar_identity(request: AvatarIdentityFusionRequest):
    try:
        service = AvatarIdentityFusionService()
        return service.fuse(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar identity fusion failed: {str(error)}"
        )
