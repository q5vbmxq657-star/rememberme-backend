from fastapi import APIRouter

from app.schemas.memory_retrieval import (
    MemoryRetrievalRequest,
    MemoryRetrievalResponse,
)

from app.services.memory_retrieval_service import (
    MemoryRetrievalService,
)

router = APIRouter()


@router.post(
    "/retrieve",
    response_model=MemoryRetrievalResponse
)
def retrieve_memories(
    request: MemoryRetrievalRequest
):
    service = MemoryRetrievalService()
    return service.retrieve(request)
