import os
import json
from openai import OpenAI

from app.schemas.conversation_memory import (
    ConversationMemorySummarizeRequest,
    ConversationMemorySummarizeResponse,
)


class ConversationMemoryService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        self.client = OpenAI(api_key=api_key)
        self.model = os.getenv("OPENAI_MEMORY_ANALYSIS_MODEL", "gpt-4o-mini")

    def summarize(
        self,
        request: ConversationMemorySummarizeRequest
    ) -> ConversationMemorySummarizeResponse:

        if not request.messages:
            return ConversationMemorySummarizeResponse(
                profile_id=request.profile_id,
                summary="",
                topics=[],
                emotional_tone=None,
                memory_worthy=False,
            )

        conversation = "\n".join(
            f"{message.role}: {message.text}"
            for message in request.messages
            if message.text.strip()
        )

        prompt = f"""
You summarize a remembrance avatar conversation.

Rules:
- Do not invent facts.
- Preserve only what was actually discussed.
- Write concise, factual summaries.
- If the conversation contains no durable memory, set memory_worthy=false.
- If the conversation contains a useful future context, set memory_worthy=true.
- Do not write as the real person.
- Return only valid JSON.

Profile:
Name: {request.profile_name}
Relationship: {request.relationship}

Conversation:
{conversation}

Return JSON:
{{
  "summary": "...",
  "topics": [],
  "emotional_tone": null,
  "memory_worthy": false
}}
"""

        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": "You return only strict JSON."},
                {"role": "user", "content": prompt},
            ],
            max_output_tokens=500,
        )

        raw = response.output_text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        data = json.loads(raw)

        return ConversationMemorySummarizeResponse(
            profile_id=request.profile_id,
            summary=str(data.get("summary", "")).strip(),
            topics=data.get("topics", []) or [],
            emotional_tone=data.get("emotional_tone"),
            memory_worthy=bool(data.get("memory_worthy", False)),
        )
