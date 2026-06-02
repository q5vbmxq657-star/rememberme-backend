from fastapi import APIRouter, HTTPException

from app.schemas.avatar_generation_job import (
    AvatarGenerationJobRequest,
    AvatarGenerationJobResponse,
)
from app.services.avatar_generation_job_service import AvatarGenerationJobService

router = APIRouter()


@router.post("/create", response_model=AvatarGenerationJobResponse)
def create_avatar_generation_job(request: AvatarGenerationJobRequest):
    try:
        service = AvatarGenerationJobService()
        return service.create_job(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar generation job creation failed: {str(error)}"
        )


@router.get("/{job_id}", response_model=AvatarGenerationJobResponse)
def get_avatar_generation_job(job_id: str):
    try:
        service = AvatarGenerationJobService()
        return service.get_job(job_id)
    except Exception as error:
        raise HTTPException(
            status_code=404,
            detail=f"Avatar generation job status failed: {str(error)}"
        )
