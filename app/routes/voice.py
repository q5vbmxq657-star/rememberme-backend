from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from app.services.openai_voice_service import OpenAIVoiceService

router = APIRouter()


class TTSRequest(BaseModel):
    text: str


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    try:
        service = OpenAIVoiceService()
        return await service.transcribe(file)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(error)}")


@router.post("/tts")
async def synthesize_voice(request: TTSRequest):
    try:
        service = OpenAIVoiceService()
        audio_stream = service.synthesize(request.text)

        return StreamingResponse(
            audio_stream,
            media_type="audio/mpeg"
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(error)}")
