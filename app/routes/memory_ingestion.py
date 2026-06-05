import traceback

from fastapi import APIRouter, HTTPException

from app.schemas.memory_ingestion import MemoryIngestionRequest, MemoryIngestionResponse
from app.services.memory_ingestion_service import MemoryIngestionService

router = APIRouter()


@router.post("/ingest", response_model=MemoryIngestionResponse)
def ingest_memory(request: MemoryIngestionRequest):
    try:
        service = MemoryIngestionService()
        return service.ingest(request)

    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Memory ingestion failed: {str(error)}"
        )
