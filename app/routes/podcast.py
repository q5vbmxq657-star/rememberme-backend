from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.schemas.podcast import (
    PodcastInvitationCreateRequest,
    PodcastInvitationCreateResponse,
    PodcastInvitationSummaryList,
    PodcastMemoryImportList,
    PodcastMemoryImportAcknowledgement,
    PodcastPublicMetadata,
    PodcastUploadResponse,
)
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import AuthenticatedSessionPrincipal, require_authenticated_principal
from app.services.podcast_repository import PodcastInvitationNotFound
from app.services.podcast_service import PodcastService, PodcastServiceError
from app.services.digital_human_profile_repository import DigitalHumanProfileRepository


router = APIRouter()
public_router = APIRouter()


@router.post("/invitations", response_model=PodcastInvitationCreateResponse)
async def create_invitation(
    body: PodcastInvitationCreateRequest,
    request: Request,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
) -> PodcastInvitationCreateResponse:
    require_profile_access(principal=principal, profile_id=body.profile_id)
    profile = DigitalHumanProfileRepository().require(body.profile_id)
    if not profile.consent_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission is required before shared answers can shape this person.",
        )
    web_base_url = os.getenv("PODCAST_WEB_BASE_URL", "").strip()
    if not web_base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sharing is temporarily unavailable.",
        )
    try:
        return await PodcastService().create_invitation(
            request=body,
            user_id=principal.user.user_id,
            public_web_base_url=web_base_url,
            backend_base_url=str(request.base_url).rstrip("/"),
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We could not prepare the private interview. Please try again.",
        ) from error


@router.get(
    "/profiles/{profile_id}/invitations",
    response_model=PodcastInvitationSummaryList,
)
def list_invitations(
    profile_id: UUID,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
) -> PodcastInvitationSummaryList:
    require_profile_access(principal=principal, profile_id=profile_id)
    try:
        return PodcastInvitationSummaryList(
            invitations=PodcastService().list_invitations(profile_id=profile_id)
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Private interview status is temporarily unavailable.",
        ) from error


@router.get("/profiles/{profile_id}/memories", response_model=PodcastMemoryImportList)
def list_completed_memories(
    profile_id: UUID,
    request: Request,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
) -> PodcastMemoryImportList:
    require_profile_access(principal=principal, profile_id=profile_id)
    try:
        memories = PodcastService().list_imports(
            profile_id=profile_id,
            backend_base_url=str(request.base_url).rstrip("/"),
        )
        return PodcastMemoryImportList(memories=memories)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Shared memories are temporarily unavailable.",
        ) from error


@router.post(
    "/profiles/{profile_id}/invitations/{invitation_id}/voice-training-used",
    response_model=PodcastMemoryImportAcknowledgement,
)
def acknowledge_voice_training_use(
    profile_id: UUID,
    invitation_id: UUID,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
) -> PodcastMemoryImportAcknowledgement:
    require_profile_access(principal=principal, profile_id=profile_id)
    acknowledged = PodcastService().mark_voice_training_used(
        invitation_id=invitation_id,
        profile_id=profile_id,
    )
    if not acknowledged:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This recording is not authorized for voice training.",
        )
    return PodcastMemoryImportAcknowledgement(
        invitation_id=invitation_id,
        acknowledged=True,
    )


@public_router.get("/{token}", response_model=PodcastPublicMetadata)
def public_metadata(token: str, request: Request) -> PodcastPublicMetadata:
    try:
        return PodcastService().public_metadata(
            token=token,
            backend_base_url=str(request.base_url).rstrip("/"),
        )
    except (PodcastInvitationNotFound, PodcastServiceError) as error:
        detail = error.safe_message if isinstance(error, PodcastServiceError) else "This private interview is no longer available."
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from error


@public_router.post("/{token}/upload", response_model=PodcastUploadResponse)
async def upload_response(
    token: str,
    request: Request,
    file: UploadFile = File(...),
) -> PodcastUploadResponse:
    try:
        return await PodcastService().ingest_response(
            token=token,
            file=file,
            backend_base_url=str(request.base_url).rstrip("/"),
        )
    except PodcastInvitationNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This private interview is no longer available.") from error
    except PodcastServiceError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=error.safe_message) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your answer could not be processed. Please try again.",
        ) from error


@public_router.post("/{token}/complete", response_model=PodcastUploadResponse)
async def complete_interview(
    token: str,
    request: Request,
    files: list[UploadFile] = File(...),
    speaker_confirmed_subject: bool = Form(False),
    voice_training_consent_granted: bool = Form(False),
) -> PodcastUploadResponse:
    try:
        return await PodcastService().complete_session(
            token=token,
            files=files,
            speaker_confirmed_subject=speaker_confirmed_subject,
            voice_training_consent_granted=voice_training_consent_granted,
            backend_base_url=str(request.base_url).rstrip("/"),
        )
    except PodcastInvitationNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This interview is no longer available.") from error
    except PodcastServiceError as error:
        response_status = (
            status.HTTP_409_CONFLICT
            if error.code == "processing_in_progress"
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=response_status, detail=error.safe_message) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Your story could not be processed. The recording remains in this browser for another try.",
        ) from error
