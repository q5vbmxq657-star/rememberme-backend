from pydantic import BaseModel, Field
from typing import List, Optional


class MemoryRetrievalRequest(BaseModel):
    profile_id: str
    user_message: str
    limit: int = 10


class RetrievedMemory(BaseModel):
    id: str
    title: str
    summary: str
    similarity_score: float
    original_text: Optional[str] = None


class MemoryRetrievalResponse(BaseModel):
    memories: List[RetrievedMemory] = Field(default_factory=list)
