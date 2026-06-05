from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.openai_realtime_service import openai_realtime_service


router = APIRouter(prefix="/v1/realtime", tags=["realtime"])


class RealtimeClientSecretRequest(BaseModel):
    instructions: str = "Start a warm, natural, emotionally safe realtime voice conversation."
    profile_name: Optional[str] = None
    relationship: Optional[str] = None


class RealtimeClientSecretResponse(BaseModel):
    client_secret: str
    expires_at: Optional[int] = None
    raw: Dict[str, Any]


@router.post("/client-secret", response_model=RealtimeClientSecretResponse)
async def create_realtime_client_secret(
    request: RealtimeClientSecretRequest,
) -> RealtimeClientSecretResponse:
    try:
        result = await openai_realtime_service.create_client_secret(
            instructions=request.instructions,
            profile_name=request.profile_name,
            relationship=request.relationship,
        )

        client_secret = result.get("client_secret")

        if not client_secret:
            raise RuntimeError("Realtime client secret response did not include a token.")

        return RealtimeClientSecretResponse(
            client_secret=client_secret,
            expires_at=result.get("expires_at"),
            raw=result.get("raw", {}),
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


@router.get("/health")
async def realtime_health():
    return {
        "status": "ok",
        "service": "openai-realtime",
    }