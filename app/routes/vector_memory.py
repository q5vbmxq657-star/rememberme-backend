from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from uuid import UUID

from app.schemas.vector_memory import (
    IndexMemoryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
    MemoryUsageUpdate,
)
from app.services.pgvector_memory_service import (
    PGVectorMemoryService,
    PGVectorSchemaNotReadyError,
    PGVectorInputError,
    PGVectorStaleIndexError,
)
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)

router = APIRouter()


@router.delete("/profiles/{profile_id}/memories/{memory_id}")
def delete_memory(profile_id: UUID, memory_id: str,
                  principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    profile = str(profile_id)
    require_profile_access(principal=principal, profile_id=profile)
    try:
        result = make_service().delete_memory(profile_id=profile, memory_id=memory_id)
        require_profile_access(principal=principal, profile_id=profile)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except HTTPException:
        raise
    except Exception as error:
        raise memory_runtime_error("deletion", error) from error


@router.get("/usage/{profile_id}")
def memory_usage(profile_id: str, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    require_profile_access(principal=principal, profile_id=profile_id)
    try:
        result = make_service().memory_usage(profile_id)
        require_profile_access(principal=principal, profile_id=profile_id)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except HTTPException:
        raise
    except Exception as error:
        raise memory_runtime_error("permission lookup", error) from error


@router.put("/usage")
def update_memory_usage(request: MemoryUsageUpdate, principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal)):
    profile_id = str(request.profile_id)
    require_profile_access(principal=principal, profile_id=profile_id)
    try:
        result = make_service().update_memory_usage(
            profile_id=profile_id, memory_id=request.memory_id, included=request.included,
            expected_revision=request.expected_revision,
        )
        require_profile_access(principal=principal, profile_id=profile_id)
        return result
    except HTTPException:
        raise
    except Exception as error:
        raise memory_runtime_error("permission update", error) from error


def make_service() -> PGVectorMemoryService:
    return PGVectorMemoryService()


def memory_runtime_error(operation: str, error: Exception) -> HTTPException:
    if isinstance(error, PGVectorStaleIndexError):
        return HTTPException(
            status_code=409,
            detail="A newer memory update replaced this request. Sync the current memories before retrying.",
        )

    if isinstance(error, PGVectorInputError):
        return HTTPException(
            status_code=422,
            detail="Memory batch is invalid. Use unique memory IDs belonging to this profile.",
        )

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
        result = make_service().index(request)
        require_profile_access(principal=principal, profile_id=request.profile_id)
        return result
    except HTTPException:
        raise
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
