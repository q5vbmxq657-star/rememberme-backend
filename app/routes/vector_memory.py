from fastapi import APIRouter, Depends, HTTPException

from app.schemas.vector_memory import (
    IndexMemoryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
)
from app.services.pgvector_memory_service import (
    PGVectorMemoryService,
    PGVectorSchemaNotReadyError,
)
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)

router = APIRouter()


def make_service() -> PGVectorMemoryService:
    return PGVectorMemoryService()


def memory_runtime_error(operation: str, error: Exception) -> HTTPException:
    if isinstance(error, PGVectorSchemaNotReadyError):
        return HTTPException(
            status_code=503,
            detail=(
                "Canonical memory storage is unavailable. "
                "Apply the required database migrations before retrying."
            ),
        )

    if isinstance(error, RuntimeError):
        return HTTPException(
            status_code=503,
            detail="Canonical memory storage is not configured.",
        )

    return HTTPException(
        status_code=500,
        detail=f"Vector {operation} failed.",
    )


@router.post("/index")
def index_memories(
    request: IndexMemoryRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
):
    require_profile_access(
        principal=principal,
        profile_id=request.profile_id,
    )
    try:
        return make_service().index(request)
    except Exception as error:
        raise memory_runtime_error("indexing", error) from error


@router.post("/search", response_model=SearchMemoryResponse)
def search_memories(
    request: SearchMemoryRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
):
    require_profile_access(
        principal=principal,
        profile_id=request.profile_id,
    )
    try:
        return make_service().search(request)
    except Exception as error:
        raise memory_runtime_error("search", error) from error
