from pathlib import Path
from unittest.mock import patch

import pytest

from app.routes import memory, streaming_memory, vector_memory
from app.schemas.vector_memory import SearchMemoryResponse, SearchMemoryResult
from app.services.memory_chat_retrieval_service import (
    MemoryChatRetrievalService,
    effective_retrieval_query,
)
from app.services.memory_conversation_prompt_builder import MemoryConversationPromptBuilder
from app.services.pgvector_memory_service import PGVectorMemoryService


ROOT = Path(__file__).resolve().parents[1]


class StubVectorService:
    def __init__(self, *, results=(), search_error: Exception | None = None):
        self.results = list(results)
        self.search_error = search_error
        self.listed_profile_id = None

    def search(self, request):
        if self.search_error:
            raise self.search_error
        return SearchMemoryResponse(results=self.results)

    def list_profile_memories(self, *, profile_id: str, limit: int):
        self.listed_profile_id = profile_id
        return self.results


def result(*, memory_id="memory-1", title="Sunday cake", similarity=0.8):
    return SearchMemoryResult(
        id=memory_id,
        title=title,
        summary="I baked a cake on Sunday.",
        original_text="On Sunday I baked a cake.",
        type="life_story",
        emotional_tags=[],
        confidence_score=0.9,
        similarity_score=similarity,
    )


def test_vector_route_has_one_canonical_service():
    with patch.object(PGVectorMemoryService, "__init__", return_value=None):
        assert isinstance(vector_memory.make_service(), PGVectorMemoryService)


def test_local_vector_service_is_removed():
    assert not (ROOT / "app/services/vector_memory_service.py").exists()


def test_both_chat_routes_use_the_same_retrieval_authority():
    assert isinstance(memory.retrieval_service, MemoryChatRetrievalService)
    assert isinstance(streaming_memory.retrieval_service, MemoryChatRetrievalService)

    for relative in ("app/routes/memory.py", "app/routes/streaming_memory.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "MemoryChatRetrievalService" in source
        assert "retrieve_backend_memories" not in source
        assert "return request.memories" not in source


def test_empty_profile_memory_set_keeps_chat_retrieval_valid():
    service = MemoryChatRetrievalService(service_factory=lambda: StubVectorService())
    assert service.retrieve(
        profile_id="profile-a",
        user_message="Hello",
        recent_messages=[],
        retrieval_limit=8,
    ) == []


def test_memory_retrieval_requires_a_profile_identity():
    service = MemoryChatRetrievalService(service_factory=lambda: StubVectorService())
    with pytest.raises(ValueError, match="profile_id is required"):
        service.retrieve(
            profile_id=" ",
            user_message="Hello",
            recent_messages=[],
            retrieval_limit=8,
        )


def test_embedding_failure_falls_back_to_profile_scoped_server_evidence():
    vector_service = StubVectorService(
        results=[result(similarity=0.0)],
        search_error=RuntimeError("embedding unavailable"),
    )
    service = MemoryChatRetrievalService(service_factory=lambda: vector_service)

    memories = service.retrieve(
        profile_id="profile-a",
        user_message="Tell me about the Sunday cake",
        recent_messages=[],
        retrieval_limit=8,
    )

    assert vector_service.listed_profile_id == "profile-a"
    assert [memory.id for memory in memories] == ["memory-1"]


def test_retrieval_infrastructure_failure_keeps_empty_memory_chat_available():
    service = MemoryChatRetrievalService(
        service_factory=lambda: (_ for _ in ()).throw(RuntimeError("database unavailable"))
    )

    assert service.retrieve(
        profile_id="profile-a",
        user_message="Hello",
        recent_messages=[],
        retrieval_limit=8,
    ) == []


def test_follow_up_context_does_not_duplicate_the_current_message():
    assert effective_retrieval_query(
        "And when?",
        ["user: Tell me about the cake", "assistant: On Sunday.", "user: And when?"],
    ) == "Tell me about the cake And when?"


def test_empty_memory_prompt_is_human_and_truth_preserving():
    prompt = MemoryConversationPromptBuilder.build(
        profile_name="Anna",
        relationship="grandmother",
        persona_context="Warm and direct.",
        memories=[],
        recent_messages=[],
    )

    assert "still answer the human part of the message naturally" in prompt
    assert "Never claim consciousness" in prompt
    assert "Never truncate a meaningful answer" in prompt
    assert "No relevant saved evidence was found" in prompt


def test_streaming_and_fallback_services_share_one_prompt_builder():
    for relative in (
        "app/services/openai_memory_service.py",
        "app/services/streaming_memory_service.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "MemoryConversationPromptBuilder.build(" in source
        assert "Maximum 3 short sentences" not in source


def test_streaming_and_fallback_services_share_one_bounded_openai_client():
    for relative in (
        "app/services/openai_memory_service.py",
        "app/services/streaming_memory_service.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "make_memory_chat_openai_client()" in source
        assert "OpenAI(" not in source

    factory = (ROOT / "app/services/memory_chat_openai_client.py").read_text(
        encoding="utf-8"
    )
    assert 'os.getenv("OPENAI_MEMORY_CHAT_TIMEOUT_SECONDS", "25")' in factory
    assert "timeout=timeout_seconds" in factory
    assert "max_retries=0" in factory


def test_vector_backend_selector_is_removed():
    source = (ROOT / "app/routes/vector_memory.py").read_text(encoding="utf-8")
    assert "VECTOR_MEMORY_BACKEND" not in source
    assert "from app.services.vector_memory_service import" not in source
    assert "return VectorMemoryService(" not in source
    assert "= VectorMemoryService(" not in source
