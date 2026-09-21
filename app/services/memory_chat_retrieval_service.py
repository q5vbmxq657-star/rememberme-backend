from __future__ import annotations

import os
import logging
from collections.abc import Callable, Sequence
from fastapi import HTTPException

from app.schemas.memory import MemoryItem
from app.schemas.persona import PersonaMemoryItem
from app.schemas.vector_memory import SearchMemoryRequest, SearchMemoryResult
from app.services.pgvector_memory_service import PGVectorMemoryService, PGVectorStaleIndexError


logger = logging.getLogger(__name__)


class MemoryEvidence(list):
    """List-compatible evidence with server-only provenance, never serialized as content."""

    def __init__(self, items, *, profile_id: str, verify: Callable[[], None], confirmed_address: str | None = None):
        super().__init__(items)
        self.profile_id = profile_id
        self.verify = verify
        self.confirmed_address = confirmed_address


def require_current_memory_evidence(items, *, profile_id: str) -> None:
    if isinstance(items, MemoryEvidence):
        if items.profile_id.lower() != str(profile_id).lower():
            raise PGVectorStaleIndexError("Memory evidence belongs to another profile.")
        items.verify()
    elif items:
        raise PGVectorStaleIndexError("Memory evidence has no server provenance.")


class MemoryChatRetrievalService:
    """Canonical server-authoritative retrieval for every chat transport."""

    def __init__(
        self,
        service_factory: Callable[[], PGVectorMemoryService] = PGVectorMemoryService,
    ) -> None:
        self._service_factory = service_factory

    def persona_memories(self, *, profile_id: str) -> list[PersonaMemoryItem]:
        # Unlike conversational retrieval, failed publication must not overwrite a persona.
        service = self._service_factory()
        version = service.evidence_version(profile_id)
        results = service.list_profile_memories(
            profile_id=profile_id, limit=100, require_published=True,
        )
        evidence = MemoryEvidence([PersonaMemoryItem(
            title=item.title, summary=item.summary, type=item.type,
            emotional_tags=item.emotional_tags,
        ) for item in results], profile_id=profile_id,
            verify=lambda: service.require_evidence_version(profile_id, version))
        require_current_memory_evidence(evidence, profile_id=profile_id)
        return evidence

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
            version = vector_service.evidence_version(clean_profile_id)
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
        except (HTTPException, PGVectorStaleIndexError):
            # Permission and publication fences must never become a fallback read.
            raise
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

        evidence = MemoryEvidence(self._rank(
            results=results,
            query=query,
            limit=limit,
            used_lexical_fallback=used_lexical_fallback,
        ), profile_id=clean_profile_id,
            confirmed_address=vector_service.confirmed_profile_address(clean_profile_id),
            verify=lambda: vector_service.require_evidence_version(clean_profile_id, version))
        require_current_memory_evidence(evidence, profile_id=clean_profile_id)
        return evidence

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
                confirmed_address=item.confirmed_address,
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
