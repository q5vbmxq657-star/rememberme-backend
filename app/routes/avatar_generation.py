from fastapi import (
    APIRouter,
    HTTPException,
    status,
)

from app.schemas.avatar_generation_readiness import (
    AvatarGenerationReadinessRequest,
    AvatarGenerationReadinessResponse,
)
from app.services.avatar_generation_readiness_service import (
    AvatarGenerationReadinessService,
)
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileNotFoundError,
    DigitalHumanProfileRepositoryError,
)


router = APIRouter()


@router.post(
    "/readiness",
    response_model=(
        AvatarGenerationReadinessResponse
    ),
)
def assess_avatar_generation_readiness(
    request: AvatarGenerationReadinessRequest,
) -> AvatarGenerationReadinessResponse:
    try:
        service = (
            AvatarGenerationReadinessService()
        )

        return service.assess(request)

    except DigitalHumanProfileNotFoundError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=str(error),
        ) from error

    except DigitalHumanProfileRepositoryError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar profile persistence is "
                f"unavailable: {error}"
            ),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Avatar generation readiness failed: "
                f"{error}"
            ),
        ) from error
