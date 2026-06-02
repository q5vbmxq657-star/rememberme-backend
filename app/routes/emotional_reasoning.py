from fastapi import APIRouter, HTTPException

from app.schemas.emotional_reasoning import (
    EmotionalReasoningRequest,
    EmotionalReasoningResponse,
)
from app.services.emotional_reasoning_service import EmotionalReasoningService

router = APIRouter()


@router.post("/assess", response_model=EmotionalReasoningResponse)
def assess_emotional_state(request: EmotionalReasoningRequest):
    try:
        service = EmotionalReasoningService()
        return service.assess(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Emotional reasoning failed: {str(error)}"
        )
