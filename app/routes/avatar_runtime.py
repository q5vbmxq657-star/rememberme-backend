from fastapi import APIRouter, HTTPException

from app.schemas.avatar_runtime import (
    AvatarRuntimePlanRequest,
    AvatarRuntimePlanResponse,
)
from app.services.avatar_runtime_plan_service import AvatarRuntimePlanService

router = APIRouter()


@router.post("/plan", response_model=AvatarRuntimePlanResponse)
def build_avatar_runtime_plan(request: AvatarRuntimePlanRequest):
    try:
        service = AvatarRuntimePlanService()
        return service.build(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar runtime plan failed: {str(error)}"
        )
