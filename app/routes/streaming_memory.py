from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas.streaming_memory import StreamingMemoryChatRequest
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)
from app.services.memory_chat_retrieval_service import MemoryChatRetrievalService
from app.services.streaming_memory_service import StreamingMemoryService


router = APIRouter()
retrieval_service = MemoryChatRetrievalService()


@router.post("/chat")
def stream_memory_chat(
    request: StreamingMemoryChatRequest,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
):
    profile_id = _authorized_profile_id(request.profile_id, principal)

    try:
        memories = retrieval_service.retrieve(
            profile_id=profile_id,
            user_message=request.user_message,
            recent_messages=request.recent_messages,
            retrieval_limit=request.retrieval_limit,
        )
        enriched_request = request.model_copy(update={"memories": memories})
        return StreamingResponse(
            StreamingMemoryService().stream_response(enriched_request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="We could not start that response. Please try again.",
        ) from error


def _authorized_profile_id(
    profile_id: str | None,
    principal: AuthenticatedSessionPrincipal,
) -> str:
    clean_profile_id = (profile_id or "").strip()
    if not clean_profile_id:
        raise HTTPException(
            status_code=422,
            detail="profile_id is required for canonical memory retrieval.",
        )
    require_profile_access(principal=principal, profile_id=clean_profile_id)
    return clean_profile_id
