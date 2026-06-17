from __future__ import annotations

from uuid import UUID

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    status,
)

from app.schemas.avatar_evidence import (
    AvatarEvidenceAssetResponse,
    AvatarEvidenceListResponse,
    AvatarEvidenceMutationResponse,
    AvatarEvidenceSelectionRequest,
)
from app.services.avatar_evidence_repository import (
    AvatarEvidenceConflictError,
    AvatarEvidenceNotFoundError,
    AvatarEvidenceRepository,
    AvatarEvidenceRepositoryError,
)


router = APIRouter()


def _response(asset) -> AvatarEvidenceAssetResponse:
    return AvatarEvidenceAssetResponse.model_validate(
        asset
    )


@router.get(
    "/profiles/{profile_id}/assets",
    response_model=AvatarEvidenceListResponse,
)
def list_avatar_evidence(
    profile_id: UUID,
    include_archived: bool = Query(
        default=False
    ),
) -> AvatarEvidenceListResponse:
    try:
        repository = AvatarEvidenceRepository()

        assets = repository.list_profile_assets(
            profile_id,
            include_archived=include_archived,
        )

        primary_identity = (
            repository.resolve_primary(
                profile_id,
                "identity_photo",
            )
        )

        primary_motion = (
            repository.resolve_primary(
                profile_id,
                "motion_video",
            )
        )

        primary_voice = (
            repository.resolve_primary(
                profile_id,
                "voice_sample",
            )
        )

        return AvatarEvidenceListResponse(
            profile_id=profile_id,
            assets=[
                _response(asset)
                for asset in assets
            ],
            primary_identity_asset_id=(
                primary_identity.asset_id
                if primary_identity
                else None
            ),
            primary_motion_asset_id=(
                primary_motion.asset_id
                if primary_motion
                else None
            ),
            primary_voice_asset_id=(
                primary_voice.asset_id
                if primary_voice
                else None
            ),
        )

    except AvatarEvidenceRepositoryError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar evidence persistence is "
                f"unavailable: {error}"
            ),
        ) from error


@router.get(
    "/assets/{asset_id}",
    response_model=AvatarEvidenceAssetResponse,
)
def get_avatar_evidence(
    asset_id: UUID,
) -> AvatarEvidenceAssetResponse:
    try:
        repository = AvatarEvidenceRepository()

        return _response(
            repository.require(asset_id)
        )

    except AvatarEvidenceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except AvatarEvidenceRepositoryError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar evidence persistence is "
                f"unavailable: {error}"
            ),
        ) from error


@router.post(
    "/assets/{asset_id}/selection",
    response_model=AvatarEvidenceMutationResponse,
)
def select_avatar_evidence(
    asset_id: UUID,
    request: AvatarEvidenceSelectionRequest,
) -> AvatarEvidenceMutationResponse:
    try:
        repository = AvatarEvidenceRepository()

        asset = repository.select_for_avatar(
            profile_id=request.profile_id,
            asset_id=asset_id,
            make_primary=request.make_primary,
        )

        return AvatarEvidenceMutationResponse(
            status="selected",
            asset=_response(asset),
        )

    except AvatarEvidenceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except AvatarEvidenceConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    except AvatarEvidenceRepositoryError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar evidence persistence is "
                f"unavailable: {error}"
            ),
        ) from error


@router.delete(
    "/assets/{asset_id}/selection",
    response_model=AvatarEvidenceMutationResponse,
)
def remove_avatar_evidence_selection(
    asset_id: UUID,
    profile_id: UUID = Query(...),
) -> AvatarEvidenceMutationResponse:
    try:
        repository = AvatarEvidenceRepository()

        asset = repository.remove_from_avatar(
            profile_id=profile_id,
            asset_id=asset_id,
        )

        return AvatarEvidenceMutationResponse(
            status="removed",
            asset=_response(asset),
        )

    except AvatarEvidenceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except AvatarEvidenceRepositoryError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar evidence persistence is "
                f"unavailable: {error}"
            ),
        ) from error


@router.post(
    "/assets/{asset_id}/archive",
    response_model=AvatarEvidenceMutationResponse,
)
def archive_avatar_evidence(
    asset_id: UUID,
    profile_id: UUID = Query(...),
) -> AvatarEvidenceMutationResponse:
    try:
        repository = AvatarEvidenceRepository()

        asset = repository.archive(
            profile_id=profile_id,
            asset_id=asset_id,
        )

        return AvatarEvidenceMutationResponse(
            status="archived",
            asset=_response(asset),
        )

    except AvatarEvidenceNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except AvatarEvidenceRepositoryError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar evidence persistence is "
                f"unavailable: {error}"
            ),
        ) from error
