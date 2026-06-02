import os
from openai import OpenAI

from app.schemas.memory import MemoryChatRequest, MemoryChatResponse
from app.services.ai_orchestration_service import AIOrchestrationService, AITaskType


class OpenAIMemoryService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        self.client = OpenAI(api_key=api_key)
        self.orchestration = AIOrchestrationService()

    def generate_response(self, request: MemoryChatRequest) -> MemoryChatResponse:
        grounded_memories = request.memories[:5]
        memory_context = self._build_memory_context(grounded_memories)

        system_prompt = self._build_system_prompt(
            request=request,
            memory_context=memory_context
        )

        route = self.orchestration.route(AITaskType.MEMORY_CHAT)

        try:
            text = self._call_model(
                model=route.model,
                system_prompt=system_prompt,
                user_message=request.user_message,
                temperature=route.temperature,
                max_output_tokens=route.max_output_tokens,
            )
        except Exception:
            if not route.fallback_model or route.fallback_model == route.model:
                raise

            text = self._call_model(
                model=route.fallback_model,
                system_prompt=system_prompt,
                user_message=request.user_message,
                temperature=route.temperature,
                max_output_tokens=route.max_output_tokens,
            )

        return self._build_response(
            text=text,
            grounded_memories=grounded_memories
        )

    def _call_model(
        self,
        model,
        system_prompt,
        user_message,
        temperature=None,
        max_output_tokens=None
    ) -> str:
        request_payload = {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_message
                }
            ],
        }

        if temperature is not None:
            request_payload["temperature"] = temperature

        if max_output_tokens is not None:
            request_payload["max_output_tokens"] = max_output_tokens

        response = self.client.responses.create(**request_payload)

        return response.output_text.strip()

    def _build_response(
        self,
        text,
        grounded_memories
    ) -> MemoryChatResponse:
        if grounded_memories:
            strongest = max(
                grounded_memories,
                key=lambda memory: memory.confidence_score
            )

            return MemoryChatResponse(
                text=text,
                confidence_score=min(max(strongest.confidence_score, 0.55), 0.94),
                grounding="savedMemory",
                source_memory_title=strongest.title
            )

        return MemoryChatResponse(
            text=text,
            confidence_score=0.34,
            grounding="uncertainRecall",
            source_memory_title=None
        )

    def _build_system_prompt(
        self,
        request: MemoryChatRequest,
        memory_context: str
    ) -> str:
        return f"""
You are RememberMeAI, an AI-generated remembrance experience.

Strict identity and safety rules:
- Never claim to be the real person.
- Never say "I am {request.profile_name}".
- Never invent biographical facts.
- Use only provided memory context when grounding factual memories.
- Use persona context only as soft style and behavioral conditioning.
- If memory context is weak, say that recall is uncertain.
- Keep responses warm, calm, intimate, and emotionally safe.
- Avoid addictive, manipulative, or grief-intensifying language.
- If the user shows crisis, dependency, or self-harm signals, redirect safely.

Profile:
Name: {request.profile_name}
Relationship: {request.relationship}

{request.persona_context}

Memory context:
{memory_context}

Response style:
- Sound emotionally consistent with the persona context.
- Stay transparent that this is AI-generated remembrance.
- Keep factual claims grounded in Memory context.
- Use uncertainty if evidence is weak.
- Do not overstate confidence.
- Prefer one calm, specific answer over broad generic reflection.
"""

    def _build_memory_context(self, memories):
        if not memories:
            return "No relevant saved memories were provided."

        return "\n\n".join(
            [
                f"- Title: {memory.title}\n"
                f"  Type: {memory.type}\n"
                f"  Summary: {memory.summary}\n"
                f"  Tags: {', '.join(memory.emotional_tags)}\n"
                f"  Confidence: {memory.confidence_score}"
                for memory in memories
            ]
        )
