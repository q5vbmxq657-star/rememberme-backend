from fastapi import APIRouter, UploadFile, File, HTTPException
from app.services.openai_voice_service import OpenAIVoiceService, VoiceRecordingTooLargeError

router = APIRouter()


@router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    try:
        service = OpenAIVoiceService()
        return await service.transcribe(file)
    except VoiceRecordingTooLargeError as error:
        raise HTTPException(
            status_code=413,
            detail="This recording is too large. Please use a shorter recording.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="We could not process this recording. Please try again.",
        ) from error
    finally:
        await file.close()
