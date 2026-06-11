from dotenv import load_dotenv
load_dotenv()

from app.services.avatar_runtime_livekit_safety_patch import (
    install_avatar_runtime_livekit_safety_patch,
)

install_avatar_runtime_livekit_safety_patch()


from fastapi import FastAPI

from app.routes.memory import router as memory_router
from app.routes.voice import router as voice_router
from app.routes.vector_memory import router as vector_memory_router
from app.routes.persona import router as persona_router
from app.routes.system import router as system_router
from app.routes.emotional_reasoning import router as emotional_reasoning_router
from app.routes.streaming_memory import router as streaming_memory_router
from app.routes.avatar_training import router as avatar_training_router
from app.routes.avatar_identity import router as avatar_identity_router
from app.routes.avatar_runtime import router as avatar_runtime_router
from app.routes.avatar_media import router as avatar_media_router
from app.routes.avatar_identity_fusion import router as avatar_identity_fusion_router
from app.routes.avatar_motion import router as avatar_motion_router
from app.routes.avatar_generation import router as avatar_generation_router
from app.routes.avatar_generation_job import router as avatar_generation_job_router
from app.routes.avatar_renderer_handoff import router as avatar_renderer_handoff_router
from app.routes.avatar_stub_renderer import router as avatar_stub_renderer_router
from app.routes.avatar_provider import router as avatar_provider_router
from app.routes.avatar_video import router as avatar_video_router
from app.routes.realtime import router as realtime_router
from app.routes.memory_ingestion import router as memory_ingestion_router
from app.routes.memory_retrieval import router as memory_retrieval_router
from app.routes.conversation_memory import router as conversation_memory_router
from app.routes.elevenlabs_voice import router as elevenlabs_voice_router

app = FastAPI(
    title="RememberMeAI Backend",
    version="0.16.0",
)

app.include_router(memory_router, prefix="/v1/memory", tags=["memory"])
app.include_router(streaming_memory_router, prefix="/v1/streaming-memory", tags=["streaming-memory"])
app.include_router(memory_retrieval_router, prefix="/v1/memory-retrieval", tags=["memory-retrieval"])
app.include_router(memory_ingestion_router, prefix="/v1/memory-ingestion", tags=["memory-ingestion"])
app.include_router(conversation_memory_router, prefix="/v1/conversation-memory", tags=["conversation-memory"])

app.include_router(voice_router, prefix="/v1/voice", tags=["voice"])
app.include_router(vector_memory_router, prefix="/v1/vector-memory", tags=["vector-memory"])
app.include_router(persona_router, prefix="/v1/persona", tags=["persona"])
app.include_router(system_router, prefix="/v1/system", tags=["system"])
app.include_router(emotional_reasoning_router, prefix="/v1/emotional-reasoning", tags=["emotional-reasoning"])

app.include_router(avatar_training_router, prefix="/v1/avatar-training", tags=["avatar-training"])
app.include_router(avatar_identity_router, prefix="/v1/avatar-identity", tags=["avatar-identity"])
app.include_router(avatar_runtime_router, prefix="/v1/avatar-runtime", tags=["avatar-runtime"])
app.include_router(avatar_media_router, prefix="/v1/avatar-media", tags=["avatar-media"])
app.include_router(avatar_identity_fusion_router, prefix="/v1/avatar-identity-fusion", tags=["avatar-identity-fusion"])
app.include_router(avatar_motion_router, prefix="/v1/avatar-motion", tags=["avatar-motion"])
app.include_router(avatar_generation_router, prefix="/v1/avatar-generation", tags=["avatar-generation"])
app.include_router(avatar_generation_job_router, prefix="/v1/avatar-generation-job", tags=["avatar-generation-job"])
app.include_router(avatar_renderer_handoff_router, prefix="/v1/avatar-renderer-handoff", tags=["avatar-renderer-handoff"])
app.include_router(avatar_stub_renderer_router, prefix="/v1/avatar-preview-renderer", tags=["avatar-preview-renderer"])
app.include_router(avatar_stub_renderer_router, prefix="/v1/avatar-stub-renderer", tags=["avatar-stub-renderer-legacy"])

app.include_router(avatar_provider_router)
app.include_router(avatar_video_router)
app.include_router(realtime_router)
app.include_router(elevenlabs_voice_router)


@app.get("/health")
def health():
    return {"status": "ok"}
