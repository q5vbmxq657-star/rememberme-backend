from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.services.elevenlabs_voice_service import elevenlabs_voice_service

router = APIRouter(prefix="/v1/elevenlabs", tags=["elevenlabs"])


class ElevenLabsTTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None
    model_id: Optional[str] = None


@router.get("/health")
async def health():
    return await elevenlabs_voice_service.health()


@router.get("/voices")
async def voices():
    try:
        return await elevenlabs_voice_service.list_voices()
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/clone-voice")
async def clone_voice(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
):
    try:
        return await elevenlabs_voice_service.clone_voice(
            name=name,
            description=description,
            files=files,
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/tts")
async def text_to_speech(request: ElevenLabsTTSRequest):
    try:
        audio = await elevenlabs_voice_service.text_to_speech(
            text=request.text,
            voice_id=request.voice_id,
            model_id=request.model_id,
        )

        return Response(
            content=audio,
            media_type="audio/mpeg",
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))