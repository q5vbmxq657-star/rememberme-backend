from pydantic import BaseModel, Field, ConfigDict
from uuid import UUID
from typing import List, Optional
from app.schemas.memory import ConfirmedAddress


class VectorMemoryItem(BaseModel):
    id: str
    profile_id: str
    title: str
    summary: str
    type: str
    emotional_tags: List[str] = Field(default_factory=list)
    confidence_score: float = 0.0
    original_text: Optional[str] = None
    confirmed_address: ConfirmedAddress = None


class IndexMemoryRequest(BaseModel):
    profile_id: str
    memories: List[VectorMemoryItem]
    excluded_memory_ids: List[str] = Field(default_factory=list, max_length=20000)


class MemoryUsageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: UUID
    memory_id: str = Field(min_length=1, max_length=200, pattern=r"^\S(?:.*\S)?$")
    included: bool
    expected_revision: int = Field(ge=0, le=9223372036854775806)


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
    original_text: Optional[str] = None
    confirmed_address: ConfirmedAddress = None


class SearchMemoryResponse(BaseModel):
    results: List[SearchMemoryResult]
