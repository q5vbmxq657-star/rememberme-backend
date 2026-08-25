from fastapi import APIRouter, Depends

from app.schemas.memory_retrieval import (
    MemoryRetrievalRequest,
    MemoryRetrievalResponse,
)

from app.services.memory_retrieval_service import (
    MemoryRetrievalService,
)
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)

router = APIRouter()


@router.post(
    "/retrieve",
    response_model=MemoryRetrievalResponse
)
def retrieve_memories(
    request: MemoryRetrievalRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
):
    require_profile_access(
        principal=principal,
        profile_id=request.profile_id,
    )
    service = MemoryRetrievalService()
    return service.retrieve(request)
