import json
from collections.abc import Generator

from app.schemas.emotional_reasoning import EmotionalReasoningRequest
from app.schemas.streaming_memory import StreamingMemoryChatRequest
from app.services.ai_orchestration_service import AIOrchestrationService, AITaskType
from app.services.emotional_reasoning_service import EmotionalReasoningService
from app.services.memory_chat_openai_client import make_memory_chat_openai_client
from app.services.memory_conversation_prompt_builder import MemoryConversationPromptBuilder


class StreamingMemoryService:
    def __init__(self):
        self.client = make_memory_chat_openai_client()
        self.orchestration = AIOrchestrationService()
        self.emotional_reasoning_service = EmotionalReasoningService()

    def stream_response(
        self,
        request: StreamingMemoryChatRequest,
    ) -> Generator[str, None, None]:
        route = self.orchestration.route(AITaskType.MEMORY_CHAT)
        assessment = self.emotional_reasoning_service.assess(
            EmotionalReasoningRequest(
                user_message=request.user_message,
                recent_messages=request.recent_messages,
                profile_name=request.profile_name,
                relationship=request.relationship,
            )
        )
        emotional_mode = request.emotional_mode or assessment.recommended_mode

        yield self._event(
            "metadata",
            {
                "status": "started",
                "model": route.model,
                "latency_profile": route.latency_profile.value,
                "emotional_mode": emotional_mode,
                "emotional_assessment": {
                    "emotional_intensity": assessment.emotional_intensity,
                    "dependency_risk": assessment.dependency_risk,
                    "crisis_risk": assessment.crisis_risk,
                    "signals": assessment.signals,
                    "guidance": assessment.guidance,
                },
            },
        )

        if emotional_mode == "crisis_redirect":
            yield self._event("delta", {"text": self._crisis_response(request.user_message)})
            yield self._event("done", {"status": "completed", "mode": emotional_mode})
            return

        prompt = MemoryConversationPromptBuilder.build(
            profile_name=request.profile_name,
            relationship=request.relationship,
            persona_context=request.persona_context,
            memories=request.memories,
            recent_messages=request.recent_messages,
            emotional_mode=emotional_mode,
            emotional_guidance=assessment.guidance,
        )

        emitted_text = False
        emitted_done = False

        try:
            stream = self.client.responses.create(
                model=route.model,
                input=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": request.user_message},
                ],
                temperature=route.temperature,
                max_output_tokens=route.max_output_tokens,
                stream=True,
            )

            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        emitted_text = True
                        yield self._event("delta", {"text": delta})
                elif event_type == "response.completed":
                    emitted_done = True
                    yield self._event(
                        "done",
                        {"status": "completed", "mode": emotional_mode},
                    )

            if not emitted_text:
                raise RuntimeError("The model returned an empty streaming response.")
            if not emitted_done:
                yield self._event(
                    "done",
                    {"status": "completed", "mode": emotional_mode},
                )
        except Exception:
            yield self._event(
                "error",
                {
                    "status": "failed",
                    "message": "We could not complete that response. Please try again.",
                },
            )

    @staticmethod
    def _crisis_response(user_message: str) -> str:
        normalized = user_message.lower()
        looks_german = any(
            marker in normalized
            for marker in (" ich ", "mir", "nicht mehr", "hilfe", "leben", "sterben")
        )
        if looks_german:
            return (
                "Es tut mir leid, dass es gerade so weh tut. Bitte bleib damit nicht allein: "
                "Ruf jetzt einen Menschen an, dem du vertraust, oder den örtlichen Notruf, "
                "wenn du in unmittelbarer Gefahr bist."
            )
        return (
            "I’m sorry this hurts so much. Please do not stay alone with it: call someone "
            "you trust now, or your local emergency number if you may be in immediate danger."
        )

    @staticmethod
    def _event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
