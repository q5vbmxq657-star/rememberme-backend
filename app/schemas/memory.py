from pydantic import BaseModel, Field, BeforeValidator
from typing import Annotated, List, Optional
import unicodedata
from uuid import UUID


def _normalize_confirmed_address(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Confirmed address must be text or null.")
    if any(unicodedata.category(character).startswith('C') or character in '\u2028\u2029' for character in value):
        raise ValueError("Confirmed address must be a single line without control characters.")
    normalized = unicodedata.normalize("NFC", value)
    return " ".join(normalized.split()) or None


ConfirmedAddress = Annotated[Optional[str], Field(max_length=80), BeforeValidator(_normalize_confirmed_address)]


class MemoryItem(BaseModel):
    id: str
    title: str
    summary: str
    original_text: Optional[str] = None
    confirmed_address: ConfirmedAddress = None
    type: str
    emotional_tags: List[str] = Field(default_factory=list)
    confidence_score: float = 0.0


class MemoryChatRequest(BaseModel):
    conversation_id: Optional[UUID] = None
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
