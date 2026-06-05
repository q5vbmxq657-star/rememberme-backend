from pydantic import BaseModel, Field
from typing import List, Optional


class ConversationMemoryMessage(BaseModel):
    role: str
    text: str


class ConversationMemorySummarizeRequest(BaseModel):
    profile_id: str
    profile_name: str
    relationship: str
    messages: List[ConversationMemoryMessage] = Field(default_factory=list)


class ConversationMemorySummarizeResponse(BaseModel):
    profile_id: str
    summary: str
    topics: List[str] = Field(default_factory=list)
    emotional_tone: Optional[str] = None
    memory_worthy: bool = False
