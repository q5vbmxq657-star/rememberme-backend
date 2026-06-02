import os
import json
from typing import Generator
from openai import OpenAI

from app.schemas.streaming_memory import StreamingMemoryChatRequest
from app.schemas.emotional_reasoning import EmotionalReasoningRequest
from app.services.ai_orchestration_service import AIOrchestrationService, AITaskType
from app.services.emotional_reasoning_service import EmotionalReasoningService


class StreamingMemoryService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        self.client = OpenAI(api_key=api_key)
        self.orchestration = AIOrchestrationService()
        self.emotional_reasoning_service = EmotionalReasoningService()

    def stream_response(
        self,
        request: StreamingMemoryChatRequest
    ) -> Generator[str, None, None]:
        route = self.orchestration.route(AITaskType.MEMORY_CHAT)
        emotional_assessment = self._assess_emotional_state(request)
        emotional_mode = request.emotional_mode or emotional_assessment.recommended_mode

        yield self._event(
            event="metadata",
            data={
                "status": "started",
                "model": route.model,
                "latency_profile": route.latency_profile.value,
                "emotional_mode": emotional_mode,
                "emotional_assessment": {
                    "emotional_intensity": emotional_assessment.emotional_intensity,
                    "dependency_risk": emotional_assessment.dependency_risk,
                    "crisis_risk": emotional_assessment.crisis_risk,
                    "signals": emotional_assessment.signals,
                    "guidance": emotional_assessment.guidance,
                }
            }
        )

        if emotional_mode == "crisis_redirect":
            crisis_text = (
                "I’m really sorry you’re feeling this much pain. "
                "This AI memory experience cannot replace immediate human support. "
                "Please contact someone you trust right now, or reach your local emergency number "
                "if you may be in danger."
            )

            yield self._event(
                event="delta",
                data={"text": crisis_text}
            )

            yield self._event(
                event="done",
                data={"status": "completed", "mode": "crisis_redirect"}
            )
            return

        system_prompt = self._build_system_prompt(
            request=request,
            emotional_mode=emotional_mode,
            emotional_guidance=emotional_assessment.guidance
        )

        try:
            stream = self.client.responses.create(
                model=route.model,
                input=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": request.user_message
                    }
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
                        yield self._event(
                            event="delta",
                            data={"text": delta}
                        )

                elif event_type == "response.completed":
                    yield self._event(
                        event="done",
                        data={
                            "status": "completed",
                            "mode": emotional_mode
                        }
                    )

        except Exception as error:
            yield self._event(
                event="error",
                data={
                    "status": "failed",
                    "message": str(error)
                }
            )

    def _assess_emotional_state(self, request: StreamingMemoryChatRequest):
        return self.emotional_reasoning_service.assess(
            EmotionalReasoningRequest(
                user_message=request.user_message,
                recent_messages=request.recent_messages,
                profile_name=request.profile_name,
                relationship=request.relationship,
            )
        )

    def _build_system_prompt(
        self,
        request: StreamingMemoryChatRequest,
        emotional_mode: str,
        emotional_guidance: str
    ) -> str:
        memory_context = self._build_memory_context(request.memories[:5])

        return f"""
You are RememberMeAI, an AI-generated remembrance experience.

Strict rules:
- Never claim to be the real person.
- Never say "I am {request.profile_name}".
- Never invent biographical facts.
- Use only provided memory context for factual grounding.
- Use persona context only as soft style and behavioral conditioning.
- If memory evidence is weak, say recall is uncertain.
- Keep the response calm, warm, concise and emotionally safe.
- Do not intensify grief or emotional dependency.
- Stay transparent that this is AI-generated remembrance.

Profile:
Name: {request.profile_name}
Relationship: {request.relationship}

Emotional response mode:
{emotional_mode}

Emotional safety guidance:
{emotional_guidance}

Persona context:
{request.persona_context}

Memory context:
{memory_context}

Mode behavior:
- normal: answer naturally, calmly and grounded.
- gentle: slow down, validate lightly, avoid emotional intensity.
- protected: avoid immersive roleplay, avoid attachment escalation, encourage real-world support.
- crisis_redirect: handled before generation.

Response style:
- One calm, specific answer.
- Prefer grounded detail over broad reflection.
- Use uncertainty where needed.
- No markdown unless clearly helpful.
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

    def _event(self, event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
