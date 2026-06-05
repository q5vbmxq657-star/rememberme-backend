import os

from app.schemas.memory_retrieval import (
    MemoryRetrievalRequest,
    MemoryRetrievalResponse,
    RetrievedMemory,
)

from app.schemas.vector_memory import (
    SearchMemoryRequest,
)

from app.services.pgvector_memory_service import (
    PGVectorMemoryService,
)


class MemoryRetrievalService:

    def __init__(self):
        self.vector_service = PGVectorMemoryService()
        self.min_similarity = float(
            os.getenv("MEMORY_RETRIEVAL_MIN_SIMILARITY", "0.24")
        )
        self.max_results = int(
            os.getenv("MEMORY_RETRIEVAL_MAX_RESULTS", "6")
        )

    def retrieve(
        self,
        request: MemoryRetrievalRequest
    ) -> MemoryRetrievalResponse:

        requested_limit = max(1, request.limit)

        vector_results = self.vector_service.search(
            SearchMemoryRequest(
                profile_id=request.profile_id,
                query=request.user_message,
                limit=max(requested_limit, self.max_results)
            )
        )

        memories = []

        for item in vector_results.results:
            if item.similarity_score < self.min_similarity:
                continue

            memories.append(
                RetrievedMemory(
                    id=item.id,
                    title=item.title,
                    summary=item.summary,
                    similarity_score=item.similarity_score,
                    original_text=item.original_text
                )
            )

            if len(memories) >= min(requested_limit, self.max_results):
                break

        return MemoryRetrievalResponse(
            memories=memories
        )
