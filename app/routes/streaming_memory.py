from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas.streaming_memory import StreamingMemoryChatRequest
from app.services.streaming_memory_service import StreamingMemoryService

router = APIRouter()


@router.post("/chat")
def stream_memory_chat(request: StreamingMemoryChatRequest):
    try:
        service = StreamingMemoryService()

        return StreamingResponse(
            service.stream_response(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Streaming memory chat failed: {str(error)}"
        )
