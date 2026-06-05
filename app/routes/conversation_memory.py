from fastapi import APIRouter, HTTPException

from app.schemas.conversation_memory import (
    ConversationMemorySummarizeRequest,
    ConversationMemorySummarizeResponse,
)
from app.services.conversation_memory_service import ConversationMemoryService

router = APIRouter()


@router.post("/summarize", response_model=ConversationMemorySummarizeResponse)
def summarize_conversation(request: ConversationMemorySummarizeRequest):
    try:
        service = ConversationMemoryService()
        return service.summarize(request)

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Conversation memory summarization failed: {str(error)}"
        )
