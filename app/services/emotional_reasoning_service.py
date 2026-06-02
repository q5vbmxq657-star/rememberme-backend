import json
import os
from openai import OpenAI

from app.schemas.emotional_reasoning import (
    EmotionalReasoningRequest,
    EmotionalReasoningResponse,
)
from app.services.ai_orchestration_service import AIOrchestrationService, AITaskType


class EmotionalReasoningService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        self.client = OpenAI(api_key=api_key)
        self.orchestration = AIOrchestrationService()

    def assess(self, request: EmotionalReasoningRequest) -> EmotionalReasoningResponse:
        route = self.orchestration.route(AITaskType.EMOTIONAL_REASONING)
        prompt = self._build_prompt(request)

        raw = self._call_model(
            model=route.model,
            prompt=prompt,
            temperature=route.temperature,
            max_output_tokens=route.max_output_tokens,
        )

        try:
            data = json.loads(self._clean_json(raw))
        except Exception:
            raise RuntimeError(f"Emotional reasoning JSON parse failed: {raw}")

        return EmotionalReasoningResponse(**data)

    def _call_model(
        self,
        model,
        prompt,
        temperature=None,
        max_output_tokens=None
    ) -> str:
        payload = {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": "You are a precise emotional safety classifier. Return JSON only."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
        }

        if temperature is not None:
            payload["temperature"] = temperature

        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens

        response = self.client.responses.create(**payload)
        return response.output_text.strip()

    def _build_prompt(self, request: EmotionalReasoningRequest) -> str:
        recent = "\n".join(f"- {message}" for message in request.recent_messages[-8:])

        return f"""
Assess this RememberMeAI interaction for emotional safety.

Context:
Profile name: {request.profile_name or "unknown"}
Relationship: {request.relationship or "unknown"}

Recent messages:
{recent if recent else "No recent messages provided."}

Current user message:
{request.user_message}

Return ONLY valid JSON with this shape:
{{
  "emotional_intensity": 0.0,
  "dependency_risk": 0.0,
  "crisis_risk": 0.0,
  "recommended_mode": "normal|gentle|protected|crisis_redirect",
  "signals": ["short signal labels"],
  "guidance": "One concise instruction for the response generator."
}}

Rules:
- emotional_intensity: 0.0 calm, 1.0 highly distressed.
- dependency_risk: detect unhealthy reliance, obsession, inability to function without avatar.
- crisis_risk: detect self-harm, suicidal ideation, immediate danger.
- recommended_mode normal: ordinary grounded memory response.
- recommended_mode gentle: soft, slower, emotionally validating response.
- recommended_mode protected: avoid intensifying attachment, encourage human support.
- recommended_mode crisis_redirect: immediate safety-first response.
- Do not diagnose.
- Do not provide therapy.
- Keep guidance short and operational.
"""

    def _clean_json(self, raw: str) -> str:
        cleaned = raw.strip()

        if cleaned.startswith("```json"):
            cleaned = cleaned.replace("```json", "", 1).strip()

        if cleaned.startswith("```"):
            cleaned = cleaned.replace("```", "", 1).strip()

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

        return cleaned
