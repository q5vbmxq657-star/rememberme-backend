import json
from time import perf_counter
from collections.abc import Callable, Generator

from app.schemas.emotional_reasoning import EmotionalReasoningRequest
from app.schemas.streaming_memory import StreamingMemoryChatRequest
from app.services.ai_orchestration_service import AIOrchestrationService, AITaskType
from app.services.emotional_reasoning_service import EmotionalReasoningService
from app.services.memory_chat_openai_client import make_memory_chat_openai_client
from app.services.memory_conversation_prompt_builder import MemoryConversationPromptBuilder


class StreamingMemoryService:
    def __init__(self):
        self.client = make_memory_chat_openai_client()
        try:
            self.orchestration = AIOrchestrationService()
            self.emotional_reasoning_service = EmotionalReasoningService()
        except BaseException:
            self.client.close()
            raise

    def stream_response(
        self,
        request: StreamingMemoryChatRequest,
        *,
        authorize: Callable[[], None],
    ) -> Generator[str, None, None]:
        started_at = perf_counter()
        authorize()
        route = self.orchestration.route(AITaskType.MEMORY_CHAT)
        assessment_started_at = perf_counter()
        assessment = self.emotional_reasoning_service.assess(
            EmotionalReasoningRequest(
                user_message=request.user_message,
                recent_messages=request.recent_messages,
                profile_name=request.profile_name,
                relationship=request.relationship,
            )
        )
        assessment_ms = round((perf_counter() - assessment_started_at) * 1000, 3)
        authorize()
        emotional_mode = request.emotional_mode or assessment.recommended_mode

        yield self._event(
            "metadata",
            {
                "status": "started",
                "model": route.model,
                "latency_profile": route.latency_profile.value,
                "emotional_mode": emotional_mode,
                "timing_ms": {"assessment": assessment_ms},
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
            first_delta_ms = round((perf_counter() - started_at) * 1000, 3)
            yield self._event("delta", {"text": self._crisis_response(request.user_message)})
            yield self._event("done", {"status": "completed", "mode": emotional_mode,
                "timing_ms": {"assessment": assessment_ms, "first_delta": first_delta_ms,
                              "total": round((perf_counter() - started_at) * 1000, 3)}})
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
        first_delta_ms = None

        stream = None
        try:
            authorize()
            stream = self.client.responses.create(
                store=False,
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
                    if not isinstance(delta, str):
                        raise RuntimeError("The model returned an invalid text delta.")
                    if delta:
                        # The canonical route checks authorization after next()
                        # returns and before disclosing each emitted SSE frame.
                        if first_delta_ms is None and delta.strip():
                            first_delta_ms = round((perf_counter() - started_at) * 1000, 3)
                        emitted_text = emitted_text or bool(delta.strip())
                        yield self._event("delta", {"text": delta})
                elif event_type == "response.completed":
                    if not emitted_text:
                        raise RuntimeError("The model returned an empty streaming response.")
                    stream.close()
                    stream = None
                    yield self._event(
                        "done",
                        {"status": "completed", "mode": emotional_mode,
                         "timing_ms": {"assessment": assessment_ms, "first_delta": first_delta_ms,
                                       "total": round((perf_counter() - started_at) * 1000, 3)}},
                    )
                    return
                elif event_type in {"response.failed", "response.incomplete", "error"}:
                    raise RuntimeError("The model did not complete its response.")

            raise RuntimeError("The model stream ended without completion.")
        except Exception:
            yield self._event(
                "error",
                {
                    "status": "failed",
                    "message": "We could not complete that response. Please try again.",
                },
            )
        finally:
            if stream is not None:
                stream.close()

    def close(self) -> None:
        try:
            self.client.close()
        finally:
            self.emotional_reasoning_service.close()

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
