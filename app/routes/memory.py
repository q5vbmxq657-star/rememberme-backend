from fastapi import APIRouter, Depends, HTTPException

from app.schemas.memory import MemoryChatRequest, MemoryChatResponse
from app.security.profile_authorization import require_profile_access
from app.security.purpose_authorization import require_profile_purposes
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)
from app.services.memory_chat_retrieval_service import MemoryChatRetrievalService
from app.services.pgvector_memory_service import PGVectorStaleIndexError
from app.services.openai_memory_service import OpenAIMemoryService
from app.services.memory_conversation_history import MemoryConversationHistoryService


router = APIRouter()
retrieval_service = MemoryChatRetrievalService()


@router.post("/chat", response_model=MemoryChatResponse)
def memory_chat(
    request: MemoryChatRequest,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
):
    profile_id = _authorized_profile_id(request.profile_id, principal)
    consent = require_profile_purposes(profile_id, {"memory_context"})

    try:
        history = MemoryConversationHistoryService()
        enriched_request, context, authorize_context = history.prepare(
            request, principal=principal, retrieval_service=retrieval_service)
        def authorize_evidence():
            require_profile_access(principal=principal, profile_id=profile_id)
            require_profile_purposes(profile_id, {"memory_context"}, expected_revision=consent.revision)
            authorize_context()
        authorize_evidence()
        result = OpenAIMemoryService().generate_response(
            enriched_request,
            authorize=authorize_evidence,
        )
        history.complete(context, request=enriched_request,
                         assistant_message=result.text, authorize=authorize_evidence)
        authorize_evidence()
        return result
    except HTTPException:
        raise
    except PGVectorStaleIndexError as error:
        raise HTTPException(status_code=409, detail="Memory settings changed. Please try again.") from error
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
