from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class MemoryIngestionRequest(BaseModel):
    profile_id: str
    asset_id: str
    asset_type: str
    title: str
    text: Optional[str] = None
    user_context: Optional[str] = None


class MemoryIngestionResponse(BaseModel):
    profile_id: str
    asset_id: str
    title: str
    asset_type: str

    summary: str
    original_text: Optional[str] = None
    avatar_memory_text: Optional[str] = None

    memory_type: str
    emotional_tags: List[str] = Field(default_factory=list)
    extracted_topics: List[str] = Field(default_factory=list)
    persona_signals: List[str] = Field(default_factory=list)
    timeline_date_hint: Optional[str] = None
    confidence_score: float
    transcript: Optional[str] = None
    visual_description: Optional[str] = None
    readiness_signals: Dict = Field(default_factory=dict)
