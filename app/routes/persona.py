from fastapi import APIRouter, HTTPException
from app.schemas.persona import PersonaExtractionRequest, PersonaExtractionResponse
from app.services.openai_persona_service import OpenAIPersonaService

router = APIRouter()


@router.post("/extract", response_model=PersonaExtractionResponse)
def extract_persona(request: PersonaExtractionRequest):
    try:
        service = OpenAIPersonaService()
        return service.extract(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="We could not update the avatar right now. Please try again."
        ) from error
