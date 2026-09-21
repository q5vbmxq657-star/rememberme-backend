from fastapi import APIRouter, Depends, HTTPException
from app.security.profile_authorization import require_profile_access
from app.security.purpose_authorization import require_profile_purposes
from app.security.user_auth import AuthenticatedSessionPrincipal, require_authenticated_principal
from app.schemas.persona import PersonaExtractionRequest, PersonaExtractionResponse
from app.services.openai_persona_service import OpenAIPersonaService
from app.services.memory_chat_retrieval_service import MemoryChatRetrievalService, require_current_memory_evidence
from app.services.pgvector_memory_service import PGVectorStaleIndexError

router = APIRouter()
retrieval_service = MemoryChatRetrievalService()


@router.post("/extract", response_model=PersonaExtractionResponse)
def extract_persona(
    request: PersonaExtractionRequest,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
):
    require_profile_access(principal=principal, profile_id=request.profile_id)
    consent = require_profile_purposes(request.profile_id, {"memory_context"})
    try:
        memories = retrieval_service.persona_memories(profile_id=str(request.profile_id))
        require_profile_access(principal=principal, profile_id=request.profile_id)
        require_current_memory_evidence(memories, profile_id=str(request.profile_id))
        def authorize_evidence():
            require_profile_access(principal=principal, profile_id=request.profile_id)
            require_profile_purposes(request.profile_id, {"memory_context"}, expected_revision=consent.revision)
            require_current_memory_evidence(memories, profile_id=str(request.profile_id))
        service = OpenAIPersonaService()
        result = service.extract(request.model_copy(update={"memories": memories}), authorize=authorize_evidence)
        require_profile_access(principal=principal, profile_id=request.profile_id)
        require_current_memory_evidence(memories, profile_id=str(request.profile_id))
        return result
    except HTTPException:
        raise
    except PGVectorStaleIndexError as error:
        raise HTTPException(status_code=409,
            detail="Memories are still being updated. Please retry after synchronization.") from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="We could not update the avatar right now. Please try again."
        ) from error
