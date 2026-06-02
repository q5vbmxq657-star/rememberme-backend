import os
import json
from pathlib import Path
from typing import List
import numpy as np
from openai import OpenAI

from app.schemas.vector_memory import (
    IndexMemoryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SearchMemoryResult,
    VectorMemoryItem,
)


class VectorMemoryService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        self.client = OpenAI(api_key=api_key)
        self.embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        self.store_path = Path("data/vector_memory_store.json")
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    def index(self, request: IndexMemoryRequest):
        store = self._load_store()
        profile_entries = []

        for memory in request.memories:
            content = self._memory_text(memory)
            embedding = self._embed(content)

            profile_entries.append({
                "id": memory.id,
                "profile_id": memory.profile_id,
                "title": memory.title,
                "summary": memory.summary,
                "type": memory.type,
                "emotional_tags": memory.emotional_tags,
                "confidence_score": memory.confidence_score,
                "embedding": embedding,
            })

        store[request.profile_id] = profile_entries
        self._save_store(store)

        return {
            "status": "indexed",
            "profile_id": request.profile_id,
            "count": len(profile_entries)
        }

    def search(self, request: SearchMemoryRequest) -> SearchMemoryResponse:
        store = self._load_store()
        entries = store.get(request.profile_id, [])

        if not entries:
            return SearchMemoryResponse(results=[])

        query_embedding = np.array(self._embed(request.query), dtype=np.float32)

        scored = []

        for entry in entries:
            memory_embedding = np.array(entry["embedding"], dtype=np.float32)
            similarity = self._cosine_similarity(query_embedding, memory_embedding)

            weighted_score = similarity + (float(entry.get("confidence_score", 0.0)) * 0.08)
            scored.append((weighted_score, similarity, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: List[SearchMemoryResult] = []

        for _, similarity, entry in scored[:max(1, request.limit)]:
            results.append(
                SearchMemoryResult(
                    id=entry["id"],
                    title=entry["title"],
                    summary=entry["summary"],
                    type=entry["type"],
                    emotional_tags=entry.get("emotional_tags", []),
                    confidence_score=float(entry.get("confidence_score", 0.0)),
                    similarity_score=float(similarity),
                )
            )

        return SearchMemoryResponse(results=results)

    def _embed(self, text: str) -> List[float]:
        response = self.client.embeddings.create(
            model=self.embedding_model,
            input=text
        )

        return response.data[0].embedding

    def _memory_text(self, memory: VectorMemoryItem) -> str:
        return "\n".join([
            f"Title: {memory.title}",
            f"Type: {memory.type}",
            f"Summary: {memory.summary}",
            f"Emotional tags: {', '.join(memory.emotional_tags)}",
            f"Confidence: {memory.confidence_score}",
        ])

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        denominator = np.linalg.norm(a) * np.linalg.norm(b)

        if denominator == 0:
            return 0.0

        return float(np.dot(a, b) / denominator)

    def _load_store(self):
        if not self.store_path.exists():
            return {}

        with open(self.store_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _save_store(self, store):
        with open(self.store_path, "w", encoding="utf-8") as file:
            json.dump(store, file)
