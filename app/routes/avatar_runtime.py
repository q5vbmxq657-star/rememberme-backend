from __future__ import annotations

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
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)
from pydantic import ValidationError

from app.schemas.avatar_runtime import (
    AvatarRuntimeOperationResponse,
    AvatarRuntimeSessionCreateRequest,
    AvatarRuntimeSessionResponse,
    AvatarRuntimeSpeechMetadata,
    AvatarRuntimeSpeechResponse,
)
from app.services.avatar_runtime_session_service import (
    AvatarRuntimeServiceError,
    AvatarRuntimeSessionService,
)


router = APIRouter()


@router.post(
    "/sessions",
    response_model=AvatarRuntimeSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_avatar_runtime_session(
    request: AvatarRuntimeSessionCreateRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarRuntimeSessionResponse:
    require_profile_access(
        principal=principal,
        profile_id=request.profile_id,
    )
    try:
        return (
            await AvatarRuntimeSessionService
            .shared()
            .create_session(request)
        )

    except AvatarRuntimeServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail="We could not start this avatar right now. Please try again.",
        ) from error


@router.post(
    "/sessions/{session_id}/speech",
    response_model=AvatarRuntimeSpeechResponse,
)
async def render_avatar_runtime_speech(
    session_id: str,
    metadata: str = Form(...),
    audio: UploadFile = File(...),
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarRuntimeSpeechResponse:
    try:
        parsed_metadata = (
            AvatarRuntimeSpeechMetadata
            .model_validate_json(metadata)
        )

        require_profile_access(
            principal=principal,
            profile_id=parsed_metadata.profile_id,
        )

        return (
            await AvatarRuntimeSessionService
            .shared()
            .render_speech(
                session_id=session_id,
                metadata=parsed_metadata,
                audio=audio,
            )
        )

    except ValidationError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail={
                "message": (
                    "Avatar runtime speech metadata "
                    "is invalid."
                ),
                "errors": error.errors(),
            },
        ) from error

    except AvatarRuntimeServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail="We could not animate this response right now. Please try again.",
        ) from error

    finally:
        await audio.close()


@router.post(
    "/sessions/{session_id}/interrupt",
    response_model=AvatarRuntimeOperationResponse,
)
async def interrupt_avatar_runtime_session(
    session_id: str,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarRuntimeOperationResponse:
    try:
        service = AvatarRuntimeSessionService.shared()
        require_profile_access(
            principal=principal,
            profile_id=service.require_session_profile_id(
                session_id
            ),
        )
        return (
            await service.interrupt_session(session_id)
        )

    except AvatarRuntimeServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail="We could not pause this avatar right now. Please try again.",
        ) from error


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def close_avatar_runtime_session(
    session_id: str,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> Response:
    try:
        service = AvatarRuntimeSessionService.shared()
        require_profile_access(
            principal=principal,
            profile_id=service.require_session_profile_id(
                session_id
            ),
        )
        await service.close_session(session_id)

        return Response(
            status_code=(
                status.HTTP_204_NO_CONTENT
            ),
        )

    except AvatarRuntimeServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail="We could not end this avatar session right now. Please try again.",
        ) from error


@router.get(
    "/providers/readiness",
)
def get_avatar_runtime_provider_readiness():
    return (
        AvatarRuntimeSessionService
        .shared()
        .provider_readiness_snapshot()
    )
