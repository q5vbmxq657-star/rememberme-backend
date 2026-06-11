from __future__ import annotations

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError

from app.schemas.avatar_runtime import (
    AvatarRuntimeOperationResponse,
    AvatarRuntimePlanRequest,
    AvatarRuntimePlanResponse,
    AvatarRuntimeSessionCreateRequest,
    AvatarRuntimeSessionResponse,
    AvatarRuntimeSpeechMetadata,
    AvatarRuntimeSpeechResponse,
)
from app.services.avatar_runtime_plan_service import (
    AvatarRuntimePlanService,
)
from app.services.avatar_runtime_session_service import (
    AvatarRuntimeServiceError,
    AvatarRuntimeSessionService,
)


router = APIRouter()


@router.post(
    "/plan",
    response_model=AvatarRuntimePlanResponse,
)
def build_avatar_runtime_plan(
    request: AvatarRuntimePlanRequest,
) -> AvatarRuntimePlanResponse:
    try:
        service = AvatarRuntimePlanService()
        return service.build(request)

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Avatar runtime plan failed: "
                f"{str(error)}"
            ),
        ) from error


@router.post(
    "/sessions",
    response_model=AvatarRuntimeSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_avatar_runtime_session(
    request: AvatarRuntimeSessionCreateRequest,
) -> AvatarRuntimeSessionResponse:
    try:
        return (
            await AvatarRuntimeSessionService
            .shared()
            .create_session(request)
        )

    except AvatarRuntimeServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.message,
        ) from error


@router.post(
    "/sessions/{session_id}/speech",
    response_model=AvatarRuntimeSpeechResponse,
)
async def render_avatar_runtime_speech(
    session_id: str,
    metadata: str = Form(...),
    audio: UploadFile = File(...),
) -> AvatarRuntimeSpeechResponse:
    try:
        parsed_metadata = (
            AvatarRuntimeSpeechMetadata
            .model_validate_json(metadata)
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
            detail=error.message,
        ) from error

    finally:
        await audio.close()


@router.post(
    "/sessions/{session_id}/interrupt",
    response_model=AvatarRuntimeOperationResponse,
)
async def interrupt_avatar_runtime_session(
    session_id: str,
) -> AvatarRuntimeOperationResponse:
    try:
        return (
            await AvatarRuntimeSessionService
            .shared()
            .interrupt_session(session_id)
        )

    except AvatarRuntimeServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.message,
        ) from error


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def close_avatar_runtime_session(
    session_id: str,
) -> Response:
    try:
        await (
            AvatarRuntimeSessionService
            .shared()
            .close_session(session_id)
        )

        return Response(
            status_code=(
                status.HTTP_204_NO_CONTENT
            ),
        )

    except AvatarRuntimeServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail=error.message,
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
