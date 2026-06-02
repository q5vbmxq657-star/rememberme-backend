from fastapi import APIRouter, HTTPException
from app.schemas.memory import MemoryChatRequest, MemoryChatResponse
from app.services.openai_memory_service import OpenAIMemoryService

router = APIRouter()


@router.post("/chat", response_model=MemoryChatResponse)
def memory_chat(request: MemoryChatRequest):
    try:
        service = OpenAIMemoryService()
        return service.generate_response(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Memory response failed: {str(error)}"
        )
