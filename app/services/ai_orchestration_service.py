import os
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class AITaskType(str, Enum):
    MEMORY_CHAT = "memory_chat"
    PERSONA_EXTRACTION = "persona_extraction"
    EMOTIONAL_REASONING = "emotional_reasoning"
    FAST_REPLY = "fast_reply"
    EMBEDDING = "embedding"
    TRANSCRIPTION = "transcription"
    TTS = "tts"


class AILatencyProfile(str, Enum):
    LOW_LATENCY = "low_latency"
    BALANCED = "balanced"
    HIGH_QUALITY = "high_quality"


@dataclass(frozen=True)
class AIModelRoute:
    task_type: AITaskType
    model: str
    latency_profile: AILatencyProfile
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    fallback_model: Optional[str] = None


class AIOrchestrationService:
    def route(self, task_type: AITaskType) -> AIModelRoute:
        if task_type == AITaskType.MEMORY_CHAT:
            return AIModelRoute(
                task_type=task_type,
                model=os.getenv("OPENAI_MEMORY_CHAT_MODEL", os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini")),
                fallback_model=os.getenv("OPENAI_MEMORY_CHAT_FALLBACK_MODEL", "gpt-4.1-mini"),
                latency_profile=AILatencyProfile.BALANCED,
                max_output_tokens=700,
                temperature=0.55,
            )

        if task_type == AITaskType.PERSONA_EXTRACTION:
            return AIModelRoute(
                task_type=task_type,
                model=os.getenv("OPENAI_PERSONA_MODEL", os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini")),
                fallback_model=os.getenv("OPENAI_PERSONA_FALLBACK_MODEL", "gpt-4.1-mini"),
                latency_profile=AILatencyProfile.HIGH_QUALITY,
                max_output_tokens=1200,
                temperature=0.2,
            )

        if task_type == AITaskType.EMOTIONAL_REASONING:
            return AIModelRoute(
                task_type=task_type,
                model=os.getenv("OPENAI_EMOTIONAL_REASONING_MODEL", os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1-mini")),
                fallback_model=os.getenv("OPENAI_EMOTIONAL_REASONING_FALLBACK_MODEL", "gpt-4.1-mini"),
                latency_profile=AILatencyProfile.HIGH_QUALITY,
                max_output_tokens=900,
                temperature=0.35,
            )

        if task_type == AITaskType.FAST_REPLY:
            return AIModelRoute(
                task_type=task_type,
                model=os.getenv("OPENAI_FAST_MODEL", "gpt-4.1-mini"),
                fallback_model=os.getenv("OPENAI_FAST_FALLBACK_MODEL", "gpt-4.1-mini"),
                latency_profile=AILatencyProfile.LOW_LATENCY,
                max_output_tokens=350,
                temperature=0.45,
            )

        if task_type == AITaskType.EMBEDDING:
            return AIModelRoute(
                task_type=task_type,
                model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
                fallback_model=os.getenv("OPENAI_EMBEDDING_FALLBACK_MODEL", "text-embedding-3-small"),
                latency_profile=AILatencyProfile.LOW_LATENCY,
            )

        if task_type == AITaskType.TRANSCRIPTION:
            return AIModelRoute(
                task_type=task_type,
                model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
                fallback_model=os.getenv("OPENAI_TRANSCRIBE_FALLBACK_MODEL", "gpt-4o-mini-transcribe"),
                latency_profile=AILatencyProfile.LOW_LATENCY,
            )

        if task_type == AITaskType.TTS:
            return AIModelRoute(
                task_type=task_type,
                model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
                fallback_model=os.getenv("OPENAI_TTS_FALLBACK_MODEL", "gpt-4o-mini-tts"),
                latency_profile=AILatencyProfile.LOW_LATENCY,
            )

        raise ValueError(f"Unsupported AI task type: {task_type}")

    def diagnostics(self):
        routes = [
            self.route(AITaskType.MEMORY_CHAT),
            self.route(AITaskType.PERSONA_EXTRACTION),
            self.route(AITaskType.EMOTIONAL_REASONING),
            self.route(AITaskType.FAST_REPLY),
            self.route(AITaskType.EMBEDDING),
            self.route(AITaskType.TRANSCRIPTION),
            self.route(AITaskType.TTS),
        ]

        return {
            "status": "ok",
            "routes": [
                {
                    "task_type": route.task_type.value,
                    "model": route.model,
                    "fallback_model": route.fallback_model,
                    "latency_profile": route.latency_profile.value,
                    "max_output_tokens": route.max_output_tokens,
                    "temperature": route.temperature,
                }
                for route in routes
            ]
        }
