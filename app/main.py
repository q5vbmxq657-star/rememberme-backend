from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI

from app.routes.memory import router as memory_router
from app.routes.voice import router as voice_router
from app.routes.vector_memory import router as vector_memory_router
from app.routes.persona import router as persona_router
from app.routes.system import router as system_router
from app.routes.streaming_memory import router as streaming_memory_router
from app.routes.avatar_runtime import router as avatar_runtime_router
from app.routes.avatar_media import (
    public_router as avatar_media_public_router,
    router as avatar_media_router,
)
from app.routes.avatar_evidence import router as avatar_evidence_router
from app.routes.avatar_state import router as avatar_state_router
from app.routes.avatar_provider import router as avatar_provider_router
from app.routes.avatar_video import router as avatar_video_router
from app.routes.tavus_webhook import router as tavus_webhook_router
from app.routes.realtime import router as realtime_router
from app.routes.memory_ingestion import router as memory_ingestion_router
from app.routes.memory_retrieval import router as memory_retrieval_router
from app.routes.elevenlabs_voice import router as elevenlabs_voice_router
from app.routes.auth import router as auth_router
from app.routes.profiles import router as profiles_router
from app.security.client_auth import require_client_key
from app.security.user_auth import require_authenticated_principal

app = FastAPI(
    title="RememberMeAI Backend",
    version="0.16.0",
)

authenticated = [Depends(require_authenticated_principal)]

app.include_router(
    auth_router,
    prefix="/v1/auth",
    tags=["authentication"],
)
app.include_router(
    profiles_router,
    prefix="/v1/profiles",
    tags=["profiles"],
)

app.include_router(memory_router, prefix="/v1/memory", tags=["memory"], dependencies=authenticated)
app.include_router(avatar_state_router, prefix="/v1/avatar-state", dependencies=authenticated)
app.include_router(streaming_memory_router, prefix="/v1/streaming-memory", tags=["streaming-memory"], dependencies=authenticated)
app.include_router(memory_retrieval_router, prefix="/v1/memory-retrieval", tags=["memory-retrieval"], dependencies=authenticated)
app.include_router(memory_ingestion_router, prefix="/v1/memory-ingestion", tags=["memory-ingestion"], dependencies=authenticated)

app.include_router(voice_router, prefix="/v1/voice", tags=["voice"], dependencies=authenticated)
app.include_router(vector_memory_router, prefix="/v1/vector-memory", tags=["vector-memory"], dependencies=authenticated)
app.include_router(persona_router, prefix="/v1/persona", tags=["persona"], dependencies=authenticated)
app.include_router(system_router, prefix="/v1/system", tags=["system"], dependencies=[Depends(require_client_key)])
app.include_router(avatar_runtime_router, prefix="/v1/avatar-runtime", tags=["avatar-runtime"], dependencies=authenticated)
app.include_router(avatar_media_router, prefix="/v1/avatar-media", tags=["avatar-media"], dependencies=authenticated)

app.include_router(
    avatar_media_public_router,
    prefix="/v1/avatar-media",
    tags=["avatar-media-public"],
)
app.include_router(avatar_evidence_router, prefix="/v1/avatar-evidence", tags=["avatar-evidence"], dependencies=authenticated)
app.include_router(avatar_provider_router, dependencies=authenticated)
app.include_router(avatar_video_router, dependencies=authenticated)
app.include_router(tavus_webhook_router)
app.include_router(realtime_router, dependencies=authenticated)
app.include_router(
    elevenlabs_voice_router,
    dependencies=authenticated,
)


@app.get("/health")
def health():
    return {"status": "ok"}
