from pydantic import BaseModel, Field
from typing import List, Optional


class VectorMemoryItem(BaseModel):
    id: str
    profile_id: str
    title: str
    summary: str
    type: str
    emotional_tags: List[str] = Field(default_factory=list)
    confidence_score: float = 0.0


class IndexMemoryRequest(BaseModel):
    profile_id: str
    memories: List[VectorMemoryItem]


class SearchMemoryRequest(BaseModel):
    profile_id: str
    query: str
    limit: int = 5


class SearchMemoryResult(BaseModel):
    id: str
    title: str
    summary: str
    type: str
    emotional_tags: List[str]
    confidence_score: float
    similarity_score: float


class SearchMemoryResponse(BaseModel):
    results: List[SearchMemoryResult]
