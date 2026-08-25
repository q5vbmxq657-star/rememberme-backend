from fastapi import APIRouter, Depends, HTTPException

from app.schemas.memory import MemoryChatRequest, MemoryChatResponse
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)
from app.services.memory_chat_retrieval_service import MemoryChatRetrievalService
from app.services.openai_memory_service import OpenAIMemoryService


router = APIRouter()
retrieval_service = MemoryChatRetrievalService()


@router.post("/chat", response_model=MemoryChatResponse)
def memory_chat(
    request: MemoryChatRequest,
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
        return OpenAIMemoryService().generate_response(
            request.model_copy(update={"memories": memories})
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="We could not complete that response. Please try again.",
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
