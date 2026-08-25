from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.elevenlabs_voice_service import (
    ElevenLabsVoiceConflictError,
    ElevenLabsVoiceError,
    ElevenLabsVoiceProviderError,
    ElevenLabsVoiceService,
    ElevenLabsVoiceValidationError,
    VoiceCloneSample,
)
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)


router = APIRouter(
    prefix="/v1/elevenlabs",
    tags=["elevenlabs"],
)


class ProfileVoiceTTSRequest(BaseModel):
    profile_id: UUID
    text: str = Field(
        min_length=1,
        max_length=8_000,
    )


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "elevenlabs",
    }


@router.get("/voices")
async def list_voices():
    try:
        service = ElevenLabsVoiceService()
        return await service.list_voices()

    except ElevenLabsVoiceError as error:
        raise HTTPException(
            status_code=503,
            detail="Voices are temporarily unavailable.",
        ) from error


@router.post(
    "/profiles/{profile_id}/clone",
    status_code=status.HTTP_201_CREATED,
)
async def clone_profile_voice(
    profile_id: UUID,
    display_name: str = Form(...),
    consent_verified: bool = Form(...),
    idempotency_key: str = Form(...),
    remove_background_noise: bool = Form(False),
    files: List[UploadFile] = File(...),
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
):
    require_profile_access(
        principal=principal,
        profile_id=profile_id,
    )
    uploads: List[
        VoiceCloneSample
    ] = []

    try:
        for upload in files:
            data = await upload.read()

            uploads.append(
                VoiceCloneSample(
                    filename=(
                        upload.filename
                        or "voice-sample"
                    ),
                    content_type=(
                        upload.content_type
                        or "application/octet-stream"
                    ),
                    data=data,
                )
            )

        service = ElevenLabsVoiceService()

        result = await service.clone_voice(
            profile_id=profile_id,
            display_name=display_name,
            samples=uploads,
            consent_verified=(
                consent_verified
            ),
            remove_background_noise=(
                remove_background_noise
            ),
            idempotency_key=(
                idempotency_key
            ),
        )

        return {
            "job_id": str(result.job_id),
            "profile_id":
                str(result.profile_id),
            "status": result.status,
            "voice_ready": (
                result.status == "ready"
            ),
            "requires_verification":
                result.requires_verification,
        }

    except ElevenLabsVoiceValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="This recording cannot be used for voice training.",
        ) from error

    except ElevenLabsVoiceConflictError as error:
        raise HTTPException(
            status_code=409,
            detail="Voice training is already in progress for this profile.",
        ) from error

    except ElevenLabsVoiceProviderError as error:
        raise HTTPException(
            status_code=503,
            detail="Voice training is temporarily unavailable. Please try again.",
        ) from error

    finally:
        for upload in files:
            await upload.close()


@router.get(
    "/profiles/{profile_id}/status",
)
async def profile_voice_status(
    profile_id: UUID,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
):
    require_profile_access(
        principal=principal,
        profile_id=profile_id,
    )
    service = ElevenLabsVoiceService()

    return service.status_for_profile(
        profile_id
    )


@router.post("/tts")
async def synthesize_profile_voice(
    request: ProfileVoiceTTSRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
):
    require_profile_access(
        principal=principal,
        profile_id=request.profile_id,
    )
    try:
        service = ElevenLabsVoiceService()

        synthesis = (
            await service
            .synthesize_for_profile(
                profile_id=(
                    request.profile_id
                ),
                text=request.text,
            )
        )

        return StreamingResponse(
            synthesis.audio_stream,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": (
                    "inline; "
                    "filename=rememberme-voice.mp3"
                ),
                "X-STAY-Voice-Mode": (
                    synthesis.voice_mode
                ),
            },
        )

    except ElevenLabsVoiceValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="This voice request is not valid for the selected profile.",
        ) from error

    except ElevenLabsVoiceProviderError as error:
        raise HTTPException(
            status_code=503,
            detail="Voice playback is temporarily unavailable. Please try again.",
        ) from error


@router.delete(
    "/profiles/{profile_id}/voice",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_profile_voice(
    profile_id: UUID,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> Response:
    require_profile_access(
        principal=principal,
        profile_id=profile_id,
    )
    try:
        service = ElevenLabsVoiceService()

        await service.delete_profile_voice(
            profile_id=profile_id
        )

        return Response(
            status_code=status.HTTP_204_NO_CONTENT
        )

    except ElevenLabsVoiceError as error:
        raise HTTPException(
            status_code=503,
            detail="We could not remove this voice right now. Please try again.",
        ) from error
