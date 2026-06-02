import os
from fastapi import APIRouter, HTTPException
from app.schemas.vector_memory import (
    IndexMemoryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
)
from app.services.vector_memory_service import VectorMemoryService
from app.services.pgvector_memory_service import PGVectorMemoryService

router = APIRouter()


def make_service():
    backend = os.getenv("VECTOR_MEMORY_BACKEND", "json").lower()

    if backend == "pgvector":
        return PGVectorMemoryService()

    return VectorMemoryService()


@router.post("/index")
def index_memories(request: IndexMemoryRequest):
    try:
        service = make_service()
        return service.index(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Vector indexing failed: {str(error)}"
        )


@router.post("/search", response_model=SearchMemoryResponse)
def search_memories(request: SearchMemoryRequest):
    try:
        service = make_service()
        return service.search(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Vector search failed: {str(error)}"
        )
