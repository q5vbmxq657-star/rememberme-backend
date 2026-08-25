from fastapi import APIRouter, Depends, HTTPException

from app.schemas.memory_ingestion import MemoryIngestionRequest, MemoryIngestionResponse
from app.services.memory_ingestion_service import MemoryIngestionService
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)

router = APIRouter()


@router.post("/ingest", response_model=MemoryIngestionResponse)
def ingest_memory(
    request: MemoryIngestionRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
):
    require_profile_access(
        principal=principal,
        profile_id=request.profile_id,
    )
    try:
        service = MemoryIngestionService()
        return service.ingest(request)

    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="We could not save this memory right now. Please try again."
        ) from error
