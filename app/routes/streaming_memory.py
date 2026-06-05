import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas.memory import MemoryItem
from app.schemas.streaming_memory import StreamingMemoryChatRequest
from app.schemas.vector_memory import SearchMemoryRequest
from app.services.pgvector_memory_service import PGVectorMemoryService
from app.services.streaming_memory_service import StreamingMemoryService

router = APIRouter()


def normalize(text: str) -> str:
    return (text or "").lower().strip()



def is_follow_up_query(query: str) -> bool:
    q = normalize(query)

    follow_up_patterns = [
        "und wem",
        "wem",
        "und wer",
        "wer",
        "und wann",
        "wann",
        "und wo",
        "wo",
        "und warum",
        "warum",
        "and who",
        "who",
        "and when",
        "when",
        "and where",
        "where",
        "and why",
        "why",
        "tell me more",
        "erzähl mehr",
        "mehr dazu",
        "was noch",
    ]

    return q in follow_up_patterns or len(q.split()) <= 3


def latest_user_topic_from_recent_messages(recent_messages) -> str:
    if not recent_messages:
        return ""

    for raw in reversed(recent_messages):
        if not isinstance(raw, str):
            continue

        message = raw.strip()
        lower = message.lower()

        if lower.startswith("user:"):
            return message.split(":", 1)[1].strip()

    return ""


def effective_retrieval_query(user_message: str, recent_messages) -> str:
    if not is_follow_up_query(user_message):
        return user_message

    previous_topic = latest_user_topic_from_recent_messages(recent_messages)

    if not previous_topic:
        return user_message

    return f"{previous_topic} {user_message}"

def lexical_bonus(query: str, title: str, summary: str, original_text: str) -> float:
    q = normalize(query)
    haystack = normalize(" ".join([title, summary, original_text]))

    bonus = 0.0

    keyword_groups = [
        {
            "query": ["back", "bäck", "geback", "gebacken", "bake", "baked", "cake", "cakes", "kuchen", "cook", "cooked", "kochen", "gekocht"],
            "memory": ["back", "bäck", "geback", "gebacken", "bake", "baked", "cake", "cakes", "kuchen", "cook", "cooked", "kochen", "gekocht"],
            "bonus": 0.18,
        },
        {
            "query": ["fußball", "fussball", "football", "soccer"],
            "memory": ["fußball", "fussball", "football", "soccer"],
            "bonus": 0.18,
        },
        {
            "query": ["schwester", "sister"],
            "memory": ["schwester", "sister"],
            "bonus": 0.15,
        },
        {
            "query": ["bruder", "brother"],
            "memory": ["bruder", "brother"],
            "bonus": 0.15,
        },
        {
            "query": ["geburtstag", "birthday"],
            "memory": ["geburtstag", "birthday"],
            "bonus": 0.15,
        },
    ]

    for group in keyword_groups:
        query_hit = any(token in q for token in group["query"])
        memory_hit = any(token in haystack for token in group["memory"])

        if query_hit and memory_hit:
            bonus += group["bonus"]

    query_tokens = [
        token for token in q.replace("?", " ").replace(".", " ").split()
        if len(token) >= 4
    ]

    for token in query_tokens:
        if token in haystack:
            bonus += 0.035

    return min(bonus, 0.35)


def retrieve_backend_memories(request: StreamingMemoryChatRequest):
    if not request.profile_id:
        return request.memories

    min_similarity = float(os.getenv("MEMORY_CHAT_MIN_SIMILARITY", "0.24"))
    debug = os.getenv("MEMORY_CHAT_RETRIEVAL_DEBUG", "false").lower() == "true"
    retrieval_limit = max(1, request.retrieval_limit or 5)

    try:
        vector_service = PGVectorMemoryService()

        search_response = vector_service.search(
            SearchMemoryRequest(
                profile_id=request.profile_id,
                query=effective_retrieval_query(
                    request.user_message,
                    request.recent_messages
                ),
                limit=max(10, retrieval_limit)
            )
        )

        scored = []

        for item in search_response.results:
            bonus = lexical_bonus(
                query=request.user_message,
                title=item.title,
                summary=item.summary,
                original_text=item.original_text or ""
            )

            final_score = item.similarity_score + bonus

            scored.append(
                {
                    "item": item,
                    "vector_score": item.similarity_score,
                    "bonus": bonus,
                    "final_score": final_score,
                }
            )

        scored.sort(
            key=lambda row: (
                row["final_score"],
                row["item"].confidence_score
            ),
            reverse=True
        )

        if debug:
            print("\n🔎 STREAMING MEMORY RETRIEVAL")
            print("PROFILE:", request.profile_id)
            print("QUERY:", request.user_message)
            print("EFFECTIVE QUERY:", effective_retrieval_query(request.user_message, request.recent_messages))
            for row in scored:
                item = row["item"]
                print(
                    " -",
                    item.title,
                    "| vector:",
                    round(row["vector_score"], 4),
                    "| bonus:",
                    round(row["bonus"], 4),
                    "| final:",
                    round(row["final_score"], 4),
                    "| summary:",
                    item.summary[:90]
                )

        filtered = [
            row for row in scored
            if row["final_score"] >= min_similarity
        ]

        retrieved = [
            MemoryItem(
                id=row["item"].id,
                title=row["item"].title,
                summary=row["item"].summary,
                original_text=row["item"].original_text,
                type=row["item"].type,
                emotional_tags=row["item"].emotional_tags,
                confidence_score=row["item"].confidence_score,
            )
            for row in filtered[:retrieval_limit]
        ]

        if retrieved:
            return retrieved

        if debug:
            print("⚠️ No streaming memories above threshold. Falling back to provided memories.")

        return request.memories

    except Exception as error:
        print("⚠️ Streaming retrieval failed, falling back to provided memories:", error)
        return request.memories


@router.post("/chat")
def stream_memory_chat(request: StreamingMemoryChatRequest):
    try:
        relevant_memories = retrieve_backend_memories(request)

        enriched_request = StreamingMemoryChatRequest(
            profile_name=request.profile_name,
            relationship=request.relationship,
            user_message=request.user_message,
            persona_context=request.persona_context,
            memories=relevant_memories,
            recent_messages=request.recent_messages,
            emotional_mode=request.emotional_mode,
            profile_id=request.profile_id,
            retrieval_limit=request.retrieval_limit,
        )

        service = StreamingMemoryService()

        return StreamingResponse(
            service.stream_response(enriched_request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Streaming memory chat failed: {str(error)}"
        )
