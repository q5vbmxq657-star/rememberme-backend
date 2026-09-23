import asyncio
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from app.schemas.avatar_provider import (
    AvatarProviderSubmitRequest,
    AvatarProviderSubmitResponse,
    AvatarProviderStatusResponse,
)
from app.services.avatar_provider_service import (
    AvatarProviderStatusUnavailableError,
    avatar_provider_service,
)
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileNotFoundError,
    DigitalHumanProfileRepositoryError,
)
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)

router = APIRouter(prefix="/v1/avatar-provider", tags=["avatar-provider"])


def _unavailable() -> HTTPException:
    return HTTPException(503, "Avatar training is temporarily unavailable. Please try again shortly.",
                         headers={"Retry-After": "12"})


def _response(state, response_type):
    # These are the canonical states emitted by the existing training/preview service.
    allowed = {"queued", "created", "submitted", "uploading", "training", "generating",
               "generatingPreview", "materializing", "ready", "failed", "cancelled", "stale"}
    try:
        if (not isinstance(state.status, str) or state.status not in allowed
                or not isinstance(state.external_job_id, str) or not state.external_job_id.strip()):
            raise ValueError("Invalid provider state")
        return response_type(
            external_job_id=state.external_job_id,
            external_avatar_id=state.external_avatar_id,
            status=state.status,
            preview_url=state.preview_url,
            error_message=state.error_message,
            error_code=getattr(state, 'error_code', None) if state.status in {'failed', 'cancelled', 'stale'} else None,
            recovery_action=(getattr(state, 'recovery_action', None) or 'review_setup')
                if state.status in {'failed', 'cancelled', 'stale'} else None,
            current_stage=state.current_stage,
            provider_detail_message=state.provider_detail_message,
        )
    except (AttributeError, TypeError, ValueError, ValidationError) as error:
        raise _unavailable() from error


@router.post("/submit", response_model=AvatarProviderSubmitResponse)
async def submit_avatar_provider_job(
    request: AvatarProviderSubmitRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarProviderSubmitResponse:
    await asyncio.to_thread(
        require_profile_access,
        principal=principal,
        profile_id=request.profile_id,
    )
    try:
        state = await avatar_provider_service.submit(
            provider=request.provider,
            profile_id=request.profile_id,
            package_record_id=request.package_record_id,
            package=request.package,
        )
    except DigitalHumanProfileNotFoundError as error:
        raise HTTPException(404, "Provider job was not found.") from error
    except (DigitalHumanProfileRepositoryError, AvatarProviderStatusUnavailableError, psycopg.Error) as error:
        raise _unavailable() from error
    await asyncio.to_thread(require_profile_access, principal=principal, profile_id=request.profile_id)
    response = _response(state, AvatarProviderSubmitResponse)
    if response.status != "failed":
        try:
            owner = await asyncio.to_thread(
                avatar_provider_service.require_training_job_profile_id, response.external_job_id)
            if owner != UUID(request.profile_id):
                raise HTTPException(404, "Provider job was not found.")
        except DigitalHumanProfileNotFoundError as error:
            raise HTTPException(404, "Provider job was not found.") from error
        except (DigitalHumanProfileRepositoryError, AvatarProviderStatusUnavailableError, psycopg.Error) as error:
            raise _unavailable() from error
        await asyncio.to_thread(require_profile_access, principal=principal, profile_id=request.profile_id)
    return response


@router.get("/status/{external_job_id}", response_model=AvatarProviderStatusResponse)
async def get_avatar_provider_job_status(
    external_job_id: str,
    profile_id: UUID = Query(...),
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarProviderStatusResponse:
    await asyncio.to_thread(
        require_profile_access,
        principal=principal,
        profile_id=profile_id,
    )

    try:
        job_profile_id = await asyncio.to_thread(
            avatar_provider_service.require_training_job_profile_id,
            external_job_id,
        )
    except DigitalHumanProfileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider job was not found.",
        ) from error
    except (DigitalHumanProfileRepositoryError, psycopg.Error) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provider job persistence is unavailable.",
        ) from error
    except AvatarProviderStatusUnavailableError as error:
        raise _unavailable() from error

    if job_profile_id != profile_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider job was not found.",
        )

    try:
        state = await avatar_provider_service.status(external_job_id)
    except DigitalHumanProfileNotFoundError as error:
        raise HTTPException(404, 'Provider job was not found.') from error
    except (DigitalHumanProfileRepositoryError, psycopg.Error) as error:
        raise HTTPException(503, 'Provider job persistence is unavailable.') from error
    except AvatarProviderStatusUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Avatar training status is temporarily unavailable. Please try again shortly.",
            headers={"Retry-After": "12"},
        ) from error

    await asyncio.to_thread(require_profile_access, principal=principal, profile_id=profile_id)
    response = _response(state, AvatarProviderStatusResponse)
    try:
        # A pending handle may resolve to a canonical provider ID during the await.
        # Both the original handle and any replacement must still belong to this profile.
        for identifier in dict.fromkeys((external_job_id, response.external_job_id)):
            current_owner = await asyncio.to_thread(
                avatar_provider_service.require_training_job_profile_id, identifier)
            if current_owner != profile_id:
                raise HTTPException(404, "Provider job was not found.")
    except DigitalHumanProfileNotFoundError as error:
        raise HTTPException(404, "Provider job was not found.") from error
    except (DigitalHumanProfileRepositoryError, AvatarProviderStatusUnavailableError, psycopg.Error) as error:
        raise _unavailable() from error
    await asyncio.to_thread(require_profile_access, principal=principal, profile_id=profile_id)
    return response
