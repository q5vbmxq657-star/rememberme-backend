from fastapi import APIRouter, HTTPException

from app.schemas.avatar_generation_readiness import (
    AvatarGenerationReadinessRequest,
    AvatarGenerationReadinessResponse,
)
from app.services.avatar_generation_readiness_service import AvatarGenerationReadinessService

router = APIRouter()


@router.post("/readiness", response_model=AvatarGenerationReadinessResponse)
def assess_avatar_generation_readiness(request: AvatarGenerationReadinessRequest):
    try:
        service = AvatarGenerationReadinessService()
        return service.assess(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar generation readiness failed: {str(error)}"
        )
