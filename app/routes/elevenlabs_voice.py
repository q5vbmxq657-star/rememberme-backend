from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.elevenlabs_voice_service import ElevenLabsVoiceService


router = APIRouter(prefix="/v1/elevenlabs", tags=["elevenlabs"])


class ElevenLabsTTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None
    model_id: Optional[str] = None


@router.get("/health")
async def health():
    return {"status": "ok", "service": "elevenlabs"}


@router.get("/voices")
async def list_voices():
    try:
        service = ElevenLabsVoiceService()
        return await service.list_voices()
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/tts")
async def synthesize_voice(request: ElevenLabsTTSRequest):
    try:
        service = ElevenLabsVoiceService()
        audio_stream = await service.synthesize(
            text=request.text,
            voice_id=request.voice_id,
        )

        return StreamingResponse(
            audio_stream,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": "inline; filename=rememberme-elevenlabs.mp3",
            },
        )

    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))
