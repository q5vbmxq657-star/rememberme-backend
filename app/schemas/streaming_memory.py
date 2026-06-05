from pydantic import BaseModel, Field
from typing import List, Optional
from app.schemas.memory import MemoryItem


class StreamingMemoryChatRequest(BaseModel):
    profile_name: str
    relationship: str
    user_message: str
    persona_context: str = ""
    memories: List[MemoryItem] = Field(default_factory=list)
    recent_messages: List[str] = Field(default_factory=list)
    emotional_mode: Optional[str] = None

    # Optional backend retrieval hook.
    profile_id: Optional[str] = None
    retrieval_limit: int = 5
