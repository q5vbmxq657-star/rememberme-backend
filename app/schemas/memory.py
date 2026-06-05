from pydantic import BaseModel, Field
from typing import List, Optional


class MemoryItem(BaseModel):
    id: str
    title: str
    summary: str
    original_text: Optional[str] = None
    type: str
    emotional_tags: List[str] = Field(default_factory=list)
    confidence_score: float = 0.0


class MemoryChatRequest(BaseModel):
    profile_name: str
    relationship: str
    user_message: str
    persona_context: str = "No stable persona profile has been extracted yet."
    memories: List[MemoryItem] = Field(default_factory=list)
    recent_messages: List[str] = Field(default_factory=list)

    # Optional backend retrieval hook.
    # If provided, backend can retrieve relevant memories from pgvector before generation.
    profile_id: Optional[str] = None
    retrieval_limit: int = 5


class MemoryChatResponse(BaseModel):
    text: str
    confidence_score: float
    grounding: str
    source_memory_title: Optional[str] = None
