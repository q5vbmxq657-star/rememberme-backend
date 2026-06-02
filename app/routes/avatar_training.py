from fastapi import APIRouter, HTTPException

from app.schemas.avatar_training import (
    AvatarTrainingReadinessRequest,
    AvatarTrainingReadinessResponse,
)
from app.services.avatar_training_readiness_service import AvatarTrainingReadinessService

router = APIRouter()


@router.post("/readiness", response_model=AvatarTrainingReadinessResponse)
def assess_avatar_training_readiness(request: AvatarTrainingReadinessRequest):
    try:
        service = AvatarTrainingReadinessService()
        return service.assess(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar training readiness failed: {str(error)}"
        )
