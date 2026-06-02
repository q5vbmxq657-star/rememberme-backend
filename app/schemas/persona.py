from pydantic import BaseModel, Field
from typing import List


class PersonaMemoryItem(BaseModel):
    title: str
    summary: str
    type: str
    emotional_tags: List[str] = Field(default_factory=list)


class PersonaExtractionRequest(BaseModel):
    profile_name: str
    relationship: str
    biography: str = ""
    memories: List[PersonaMemoryItem] = Field(default_factory=list)


class PersonaExtractionResponse(BaseModel):
    dominant_traits: List[str] = Field(default_factory=list)
    values: List[str] = Field(default_factory=list)
    speaking_style: List[str] = Field(default_factory=list)
    typical_phrases: List[str] = Field(default_factory=list)
    emotional_patterns: List[str] = Field(default_factory=list)
    identity_anchors: List[str] = Field(default_factory=list)
    confidence_score: float = 0.0
