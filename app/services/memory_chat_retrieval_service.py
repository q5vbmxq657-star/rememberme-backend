from __future__ import annotations

import os
import logging
from collections.abc import Callable, Sequence

from app.schemas.memory import MemoryItem
from app.schemas.vector_memory import SearchMemoryRequest, SearchMemoryResult
from app.services.pgvector_memory_service import PGVectorMemoryService


logger = logging.getLogger(__name__)


class MemoryChatRetrievalService:
    """Canonical server-authoritative retrieval for every chat transport."""

    def __init__(
        self,
        service_factory: Callable[[], PGVectorMemoryService] = PGVectorMemoryService,
    ) -> None:
        self._service_factory = service_factory

    def retrieve(
        self,
        *,
        profile_id: str,
        user_message: str,
        recent_messages: Sequence[str],
        retrieval_limit: int,
    ) -> list[MemoryItem]:
        clean_profile_id = profile_id.strip()
        if not clean_profile_id:
            raise ValueError("profile_id is required for canonical memory retrieval.")

        limit = max(1, min(retrieval_limit, 20))
        query = effective_retrieval_query(user_message, recent_messages)
        used_lexical_fallback = False

        try:
            vector_service = self._service_factory()
        except Exception:
            logger.warning(
                "Profile-scoped memory retrieval unavailable; continuing without evidence."
            )
            return []

        try:
            results = vector_service.search(
                SearchMemoryRequest(
                    profile_id=clean_profile_id,
                    query=query,
                    limit=max(10, limit),
                )
            ).results
        except Exception:
            used_lexical_fallback = True
            try:
                # A transient embedding-provider failure must not make chat unusable.
                # This fallback still reads only profile-scoped server evidence.
                results = vector_service.list_profile_memories(
                    profile_id=clean_profile_id,
                    limit=100,
                )
            except Exception:
                # Conversation remains available without evidence. The shared
                # prompt contract explicitly prevents invented memories when
                # this list is empty.
                logger.warning(
                    "Profile-scoped memory retrieval unavailable; continuing without evidence."
                )
                return []

        return self._rank(
            results=results,
            query=query,
            limit=limit,
            used_lexical_fallback=used_lexical_fallback,
        )

    def _rank(
        self,
        *,
        results: Sequence[SearchMemoryResult],
        query: str,
        limit: int,
        used_lexical_fallback: bool,
    ) -> list[MemoryItem]:
        min_similarity = (
            0.08
            if used_lexical_fallback
            else float(os.getenv("MEMORY_CHAT_MIN_SIMILARITY", "0.24"))
        )
        scored: list[tuple[float, SearchMemoryResult]] = []

        for item in results:
            score = item.similarity_score + lexical_bonus(
                query=query,
                title=item.title,
                summary=item.summary,
                original_text=item.original_text or "",
            )
            scored.append((score, item))

        scored.sort(
            key=lambda row: (row[0], row[1].confidence_score, row[1].id),
            reverse=True,
        )

        return [
            MemoryItem(
                id=item.id,
                title=item.title,
                summary=item.summary,
                original_text=item.original_text,
                type=item.type,
                emotional_tags=item.emotional_tags,
                confidence_score=item.confidence_score,
            )
            for score, item in scored
            if score >= min_similarity
        ][:limit]


def normalize(text: str) -> str:
    return (text or "").lower().strip()


def is_follow_up_query(query: str) -> bool:
    normalized = normalize(query)
    follow_ups = {
        "und wem", "wem", "und wer", "wer", "und wann", "wann",
        "und wo", "wo", "und warum", "warum", "erzähl mehr",
        "mehr dazu", "was noch", "and who", "who", "and when",
        "when", "and where", "where", "and why", "why", "tell me more",
    }
    return normalized in follow_ups or len(normalized.split()) <= 3


def effective_retrieval_query(
    user_message: str,
    recent_messages: Sequence[str],
) -> str:
    if not is_follow_up_query(user_message):
        return user_message

    for raw_message in reversed(recent_messages):
        message = raw_message.strip()
        if message.lower().startswith("user:"):
            previous_topic = message.split(":", 1)[1].strip()
            if previous_topic and normalize(previous_topic) != normalize(user_message):
                return f"{previous_topic} {user_message}"

    return user_message


def lexical_bonus(
    *,
    query: str,
    title: str,
    summary: str,
    original_text: str,
) -> float:
    query_terms = searchable_terms(query)
    if not query_terms:
        return 0.0

    memory_terms = searchable_terms(" ".join((title, summary, original_text)))
    overlap = query_terms.intersection(memory_terms)
    coverage = len(overlap) / len(query_terms)
    return min(coverage * 0.35, 0.35)


def searchable_terms(text: str) -> set[str]:
    normalized = "".join(character if character.isalnum() else " " for character in normalize(text))
    return {term for term in normalized.split() if len(term) >= 3}
