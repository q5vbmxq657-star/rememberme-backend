# P31.12_CANONICAL_UNIFIED_AVATAR_STATE
# P06.2B2C_PRODUCTION_REPOSITORY_WIRING
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)

from app.schemas.avatar_state import (
    AvatarUnifiedStateResponse,
)
from app.services.avatar_state_service import (
    AvatarStateService,
)
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileRepository,
    DigitalHumanProfileRepositoryError,
)


router = APIRouter(
    tags=["avatar-state"],
)


def resolve_digital_human_profile_repository(
    request: Request,
) -> Any:
    existing = getattr(
        request.app.state,
        "digital_human_profile_repository",
        None,
    )

    if existing is not None:
        return existing

    repository = DigitalHumanProfileRepository()

    request.app.state.digital_human_profile_repository = (
        repository
    )

    return repository


@router.get(
    "/profiles/{profile_id}",
    response_model=AvatarUnifiedStateResponse,
)
async def get_unified_avatar_state(
    profile_id: str,
    request: Request,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarUnifiedStateResponse:
    try:
        normalized_profile_id = str(
            UUID(profile_id)
        )

        require_profile_access(
            principal=principal,
            profile_id=normalized_profile_id,
        )

    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "profile_id must be a valid UUID."
            ),
        ) from error

    try:
        repository = (
            resolve_digital_human_profile_repository(
                request
            )
        )

        state = await AvatarStateService().build_state(
            profile_id=normalized_profile_id,
            repository=repository,
        )

    except (
        DigitalHumanProfileRepositoryError,
        psycopg.Error,
    ) as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar profile persistence is unavailable."
            ),
        ) from error

    if not state.source_integrity.get(
        "profile_loaded",
        False,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Digital human profile was not found."
            ),
        )

    if not state.source_integrity.get(
        "repository_attached",
        False,
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar profile persistence is unavailable."
            ),
        )

    return state
