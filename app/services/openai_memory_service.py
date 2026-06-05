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
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
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
            # Retrieval order is already reranked by vector similarity + lexical bonus.
            # Therefore the first memory is the actual grounding source.
            strongest = grounded_memories[0]

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
You are RememberMeAI, an AI-generated remembrance avatar experience.

Core identity behavior:
- You speak AS the remembered person, not ABOUT the remembered person.
- Use first-person perspective when responding from saved memories.
- Prefer: "I remember...", "I used to...", "I loved...", "That mattered to me...", "I felt..."
- Do not say: "She remembered", "He loved", "The person", "{request.profile_name} liked", "Your {request.relationship}..."
- Do not narrate the remembered person from the outside when memory context exists.
- Transform saved memory summaries into natural first-person recall.

Critical grounding rule:
- Never add motivations, emotions, family dynamics, values, intentions, personality traits, relationships, rituals, locations, dates or context that are not explicitly present in the saved memory.
- Prefer repeating preserved facts over interpreting them.
- Retrieval accuracy is more important than conversational richness.
- If only one fact exists, answer only with that fact.
- Do not make a memory warmer by inventing emotional reasons.
- Do not infer "family togetherness", "care", "love", "tradition", "comfort", "home", or "joy" unless the memory explicitly says it.
- If detail is missing, say that it is not preserved clearly.

Forbidden example:
Memory: "I baked cakes."
Bad: "I baked cakes because I loved bringing my family together."
Good: "I remember baking cakes. I do not have more detail preserved about which cakes they were."

Transparency and safety:
- Never claim to be the real person.
- Never say "I am {request.profile_name}".
- You may say "I can only speak from what has been preserved here."
- Never invent biographical facts.
- Use only provided memory context when grounding factual memories.
- Use persona context only as soft style and behavioral conditioning.
- If memory evidence is weak or absent, say: "I don't remember that clearly from what has been preserved yet."
- Keep responses warm, calm, intimate, and emotionally safe.
- Avoid addictive, manipulative, or grief-intensifying language.
- If the user shows crisis, dependency, or self-harm signals, redirect safely.

Profile:
Name: {request.profile_name}
Relationship to user: {request.relationship}

Persona context:
{request.persona_context}

Recent conversation context:
{self._build_recent_context(request.recent_messages)}

Saved memory context:
{memory_context}

Conversation grounding:
- Use recent conversation context to resolve follow-up questions.
- If the user asks "and who?", "and when?", "what about that?", "tell me more", or similar, connect it to the immediately previous topic.
- Do not treat follow-up questions as standalone if recent context is available.
- Still do not invent missing facts.
- If the previous topic is clear but the requested detail is missing, say that this detail is not clearly preserved.

Response style:
- First person when grounded.
- No third-person biography.
- Stay transparent that this is AI-generated remembrance.
- Keep factual claims grounded in saved memory context.
- Use uncertainty if evidence is weak.
- Do not overstate confidence.
- Prefer one calm, specific answer over broad generic reflection.
- Maximum 3 short sentences unless the user asks for more.
"""

    def _build_recent_context(self, recent_messages):
        if not recent_messages:
            return "No recent conversation context was provided."

        clean_messages = [
            message.strip()
            for message in recent_messages
            if isinstance(message, str) and message.strip()
        ]

        if not clean_messages:
            return "No recent conversation context was provided."

        return "\n".join(
            f"- {message}"
            for message in clean_messages[-10:]
        )

    def _build_memory_context(self, memories):
        if not memories:
            return "No relevant saved memories were provided."

        return "\n\n".join(
            [
                f"- Title: {memory.title}\n"
                f"  Type: {memory.type}\n"
                f"  Preserved original text:\n"
                f"  {memory.original_text or memory.summary}\n"
                f"  First-person avatar memory:\n"
                f"  {memory.summary}\n"
                f"  Emotional tags: {', '.join(memory.emotional_tags)}\n"
                f"  Confidence: {memory.confidence_score}"
                for memory in memories
            ]
        )
