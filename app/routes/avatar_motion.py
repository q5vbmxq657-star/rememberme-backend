from fastapi import APIRouter, HTTPException

from app.schemas.avatar_motion import (
    AvatarMotionReadinessRequest,
    AvatarMotionReadinessResponse,
)
from app.services.avatar_motion_readiness_service import AvatarMotionReadinessService

router = APIRouter()


@router.post("/readiness", response_model=AvatarMotionReadinessResponse)
def assess_avatar_motion_readiness(request: AvatarMotionReadinessRequest):
    try:
        service = AvatarMotionReadinessService()
        return service.assess(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar motion readiness failed: {str(error)}"
        )
