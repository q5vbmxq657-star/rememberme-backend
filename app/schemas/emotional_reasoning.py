from pydantic import BaseModel, Field
from typing import List, Optional


class EmotionalReasoningRequest(BaseModel):
    user_message: str
    recent_messages: List[str] = Field(default_factory=list)
    profile_name: Optional[str] = None
    relationship: Optional[str] = None


class EmotionalReasoningResponse(BaseModel):
    emotional_intensity: float
    dependency_risk: float
    crisis_risk: float
    recommended_mode: str
    signals: List[str] = Field(default_factory=list)
    guidance: str
