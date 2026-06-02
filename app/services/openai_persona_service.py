import os
import json
from openai import OpenAI

from app.schemas.persona import PersonaExtractionRequest, PersonaExtractionResponse
from app.services.ai_orchestration_service import AIOrchestrationService, AITaskType


class OpenAIPersonaService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        self.client = OpenAI(api_key=api_key)
        self.orchestration = AIOrchestrationService()

    def extract(self, request: PersonaExtractionRequest) -> PersonaExtractionResponse:
        memory_context = self._build_memory_context(request)
        prompt = self._build_prompt(
            request=request,
            memory_context=memory_context
        )

        route = self.orchestration.route(AITaskType.PERSONA_EXTRACTION)

        try:
            raw = self._call_model(
                model=route.model,
                prompt=prompt,
                temperature=route.temperature,
                max_output_tokens=route.max_output_tokens,
            )
        except Exception:
            if not route.fallback_model or route.fallback_model == route.model:
                raise

            raw = self._call_model(
                model=route.fallback_model,
                prompt=prompt,
                temperature=route.temperature,
                max_output_tokens=route.max_output_tokens,
            )

        data = self._parse_json(raw)
        return PersonaExtractionResponse(**data)

    def _call_model(
        self,
        model,
        prompt,
        temperature=None,
        max_output_tokens=None
    ) -> str:
        request_payload = {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": "You are a precise JSON-only persona extraction engine."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
        }

        if temperature is not None:
            request_payload["temperature"] = temperature

        if max_output_tokens is not None:
            request_payload["max_output_tokens"] = max_output_tokens

        response = self.client.responses.create(**request_payload)

        return response.output_text.strip()

    def _parse_json(self, raw: str):
        cleaned = raw.strip()

        if cleaned.startswith("```json"):
            cleaned = cleaned.removeprefix("```json").strip()

        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```").strip()

        if cleaned.endswith("```"):
            cleaned = cleaned.removesuffix("```").strip()

        try:
            return json.loads(cleaned)
        except Exception:
            raise RuntimeError(f"Persona JSON parse failed: {raw}")

    def _build_prompt(
        self,
        request: PersonaExtractionRequest,
        memory_context: str
    ) -> str:
        return f"""
You extract soft persona signals for an ethical AI remembrance system.

Strict rules:
- Do NOT invent facts.
- Do NOT claim the person had a trait unless there is evidence.
- Extract only soft behavioral patterns supported by the provided biography or memories.
- Keep outputs short, human-readable, and stable.
- Typical phrases should only include actual phrases if explicitly provided.
- If evidence is weak, return fewer items and lower confidence.
- Do not output markdown.
- Do not wrap JSON in code fences.

Profile:
Name: {request.profile_name}
Relationship: {request.relationship}
Biography:
{request.biography}

Memory evidence:
{memory_context}

Return ONLY valid JSON with this exact shape:
{{
  "dominant_traits": ["Warm", "Calm"],
  "values": ["Family"],
  "speaking_style": ["Soft and measured"],
  "typical_phrases": [],
  "emotional_patterns": ["Creates safety"],
  "identity_anchors": ["Sunday coffee ritual"],
  "confidence_score": 0.0
}}
"""

    def _build_memory_context(self, request: PersonaExtractionRequest) -> str:
        if not request.memories:
            return "No memories provided."

        return "\n\n".join(
            [
                f"- Title: {item.title}\n"
                f"  Type: {item.type}\n"
                f"  Summary: {item.summary}\n"
                f"  Tags: {', '.join(item.emotional_tags)}"
                for item in request.memories
            ]
        )
