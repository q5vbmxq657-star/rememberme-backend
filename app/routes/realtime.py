from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)
from app.services.openai_realtime_service import (
    openai_realtime_service,
)


router = APIRouter(
    prefix="/v1/realtime",
    tags=["realtime"],
)


class RealtimeAvatarSessionRequest(BaseModel):
    profile_id: str = Field(
        min_length=1,
        description="Client-side MemoryProfile UUID.",
    )
    profile_name: Optional[str] = None
    relationship: Optional[str] = None
    persona_context: Optional[str] = None
    memory_context: Optional[str] = None
    language: Optional[str] = "de-DE"
    instructions: Optional[str] = None
    mode: Optional[str] = "voice"


class RealtimeAvatarSessionResponse(BaseModel):
    session_type: str
    transport: str
    model: str
    voice: str
    client_secret: str
    expires_at: Optional[int] = None
    fallback_mode: str
    profile_id: str


@router.post(
    "/avatar/session",
    response_model=RealtimeAvatarSessionResponse,
)
async def create_realtime_avatar_session(
    request: RealtimeAvatarSessionRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> RealtimeAvatarSessionResponse:
    """
    Canonical RemembermeAI realtime call session broker.

    The iOS app receives only a short-lived client secret.
    The OpenAI API key remains server-side.
    """
    require_profile_access(
        principal=principal,
        profile_id=request.profile_id,
    )

    try:
        result = await openai_realtime_service.create_avatar_session(
            profile_id=request.profile_id,
            profile_name=request.profile_name,
            relationship=request.relationship,
            persona_context=request.persona_context,
            memory_context=request.memory_context,
            language=request.language,
            instructions=request.instructions,
            mode=request.mode,
        )

        client_secret = result.get("client_secret")

        if not client_secret:
            raise RuntimeError(
                "Realtime avatar session did not include a client secret."
            )

        return RealtimeAvatarSessionResponse(
            session_type="openai_realtime_avatar",
            transport="webrtc",
            model=result["model"],
            voice=result["voice"],
            client_secret=client_secret,
            expires_at=result.get("expires_at"),
            fallback_mode="native_auto_turn_voice",
            profile_id=request.profile_id,
        )

    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live conversation is temporarily unavailable.",
        ) from error


@router.get("/health")
async def realtime_health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "openai-realtime",
        "session_endpoint": "/v1/realtime/avatar/session",
        "transport": "webrtc",
        "fallback_mode": "native_auto_turn_voice",
    }
