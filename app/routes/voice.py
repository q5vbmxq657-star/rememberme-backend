from uuid import UUID
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from app.security.profile_authorization import require_profile_access
from app.security.purpose_authorization import require_profile_purposes
from app.security.user_auth import AuthenticatedSessionPrincipal, require_authenticated_principal
from app.services.openai_voice_service import OpenAIVoiceService, VoiceRecordingTooLargeError

router = APIRouter()


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    profile_id: UUID = Form(...),
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
):
    try:
        require_profile_access(principal=principal, profile_id=profile_id)
        consent = require_profile_purposes(profile_id, {"memory_context"})
        def authorize():
            require_profile_access(principal=principal, profile_id=profile_id)
            require_profile_purposes(profile_id, {"memory_context"}, expected_revision=consent.revision)
        service = OpenAIVoiceService()
        result = await service.transcribe(file, authorize=authorize)
        require_profile_access(principal=principal, profile_id=profile_id)
        require_profile_purposes(profile_id, {"memory_context"}, expected_revision=consent.revision)
        return result
    except HTTPException:
        raise
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
