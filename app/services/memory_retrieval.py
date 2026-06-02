from app.schemas.memory import MemoryItem
from typing import List
import re


class MemoryRetrievalService:
    def retrieve(self, query: str, memories: List[MemoryItem], limit: int = 3) -> List[MemoryItem]:
        terms = self._tokenize(query)
        if not terms:
            return []

        scored = []

        for memory in memories:
            searchable = " ".join([
                memory.title,
                memory.summary,
                " ".join(memory.emotional_tags),
                memory.type
            ]).lower()

            matches = sum(1 for term in terms if term in searchable)
            if matches == 0:
                continue

            score = (matches / len(terms)) + (memory.confidence_score * 0.35)
            scored.append((score, memory))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [memory for _, memory in scored[:limit]]

    def _tokenize(self, text: str) -> List[str]:
        stop_words = {
            "the", "and", "with", "that", "this", "what", "when", "where",
            "ich", "du", "der", "die", "das", "und", "oder", "mit", "ein", "eine",
            "about", "tell", "please", "bitte", "erzähl", "erinnere"
        }

        raw_terms = re.split(r"\W+", text.lower())

        return [
            term for term in raw_terms
            if len(term) > 2 and term not in stop_words
        ]
