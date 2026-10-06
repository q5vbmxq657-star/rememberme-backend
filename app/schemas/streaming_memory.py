from pydantic import BaseModel, Field
from typing import List, Optional
from app.schemas.memory import MemoryItem, ConversationAdmission
from uuid import UUID


class StreamingMemoryChatRequest(ConversationAdmission):
    request_id: Optional[UUID] = None
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
