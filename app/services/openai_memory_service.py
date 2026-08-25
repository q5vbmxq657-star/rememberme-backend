from app.schemas.memory import MemoryChatRequest, MemoryChatResponse
from app.services.ai_orchestration_service import AIOrchestrationService, AITaskType
from app.services.memory_chat_openai_client import make_memory_chat_openai_client
from app.services.memory_conversation_prompt_builder import MemoryConversationPromptBuilder


class OpenAIMemoryService:
    def __init__(self):
        self.client = make_memory_chat_openai_client()
        self.orchestration = AIOrchestrationService()

    def generate_response(self, request: MemoryChatRequest) -> MemoryChatResponse:
        grounded_memories = request.memories[:8]
        system_prompt = MemoryConversationPromptBuilder.build(
            profile_name=request.profile_name,
            relationship=request.relationship,
            persona_context=request.persona_context,
            memories=grounded_memories,
            recent_messages=request.recent_messages,
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

        return self._build_response(text=text, grounded_memories=grounded_memories)

    def _call_model(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        request_payload = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        if temperature is not None:
            request_payload["temperature"] = temperature
        if max_output_tokens is not None:
            request_payload["max_output_tokens"] = max_output_tokens

        response = self.client.responses.create(**request_payload)
        text = response.output_text.strip()
        if not text:
            raise RuntimeError("The model returned an empty memory-chat response.")
        return text

    @staticmethod
    def _build_response(
        *,
        text: str,
        grounded_memories,
    ) -> MemoryChatResponse:
        if grounded_memories:
            strongest = grounded_memories[0]
            return MemoryChatResponse(
                text=text,
                confidence_score=min(max(strongest.confidence_score, 0.55), 0.94),
                grounding="savedMemory",
                source_memory_title=strongest.title,
            )

        return MemoryChatResponse(
            text=text,
            confidence_score=0.34,
            grounding="uncertainRecall",
            source_memory_title=None,
        )
