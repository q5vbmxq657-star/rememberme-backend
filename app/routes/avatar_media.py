from __future__ import annotations

import asyncio
from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response

from app.security.profile_authorization import require_profile_access
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)

from app.schemas.avatar_media import (
    AvatarMediaListResponse,
    AvatarMediaMetadata,
    AvatarMediaSignRequest,
    AvatarMediaSignResponse,
    AvatarMediaStorageHealthResponse,
    AvatarMediaUploadResponse,
)
from app.services.avatar_evidence_repository import (
    AvatarEvidenceRepositoryError,
)
from app.services.avatar_media_analysis_service import (
    AvatarMediaAnalysisError,
    AvatarMediaAnalysisService,
)
from app.services.avatar_media_evidence_bridge_service import (
    AvatarMediaEvidenceBridgeError,
    AvatarMediaEvidenceBridgeService,
)
from app.services.avatar_media_storage_service import (
    AvatarMediaStorageService,
)


router = APIRouter()
public_router = APIRouter()
@router.post(
    "/upload",
    response_model=AvatarMediaUploadResponse,
)
async def upload_avatar_media(
    request: Request,
    profile_id: str = Form(...),
    asset_type: str = Form(...),
    title: str = Form(...),
    upload_id: Optional[str] = Form(
        default=None
    ),
    file: UploadFile = File(...),
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarMediaUploadResponse:
    storage_service = (
        AvatarMediaStorageService()
    )

    uploaded_asset_id: Optional[str] = None
    uploaded_asset_was_existing = False

    try:
        normalized_profile_id = str(
            UUID(profile_id)
        )

        require_profile_access(
            principal=principal,
            profile_id=normalized_profile_id,
        )

        request_base_url = str(
            request.base_url
        ).rstrip("/")

        response = await storage_service.upload(
            profile_id=(
                normalized_profile_id
            ),
            asset_type=asset_type,
            title=title,
            file=file,
            base_url=request_base_url,
            upload_id=upload_id,
        )

        uploaded_asset_id = (
            response.asset_id
        )
        uploaded_asset_was_existing = (
            response.was_existing
        )

        metadata = (
            storage_service.get_metadata(
                response.asset_id
            )
        )

        bridge = (
            AvatarMediaEvidenceBridgeService()
        )

        normalized_asset_type = (
            asset_type
            .strip()
            .lower()
        )

        analysis = await asyncio.to_thread(
            AvatarMediaAnalysisService().analyze,
            storage_path=metadata.storage_path,
            asset_type=normalized_asset_type,
            content_type=response.content_type,
        )

        if normalized_asset_type in {
            "image",
            "reference",
        }:
            response.face_analysis = {
                "has_face": analysis.has_face,
                "has_frontal_face": analysis.has_frontal_face,
                "has_clear_lighting": analysis.has_clear_lighting,
                "emotional_presence_score": 0.0,
                "identity_consistency_score": 0.0,
                "quality_score": analysis.quality_score,
                "recommended_for_avatar": analysis.recommended_for_avatar,
                "analysis_version": analysis.analysis_version,
                "analysis_metadata": analysis.analysis_metadata,
            }
        else:
            response.face_analysis = None

        if normalized_asset_type not in {
            "memory_image",
            "memory_video",
        }:
            bridge.persist_uploaded_media(
                metadata=metadata,
                analysis=analysis,
            )

        return response

    except ValueError as error:
        if uploaded_asset_id and not uploaded_asset_was_existing:
            storage_service.delete_asset(
                uploaded_asset_id
            )

        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "profile_id must be a valid UUID."
            ),
        ) from error

    except AvatarMediaEvidenceBridgeError as error:
        if uploaded_asset_id and not uploaded_asset_was_existing:
            storage_service.delete_asset(
                uploaded_asset_id
            )

        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail="This media could not be used for the selected profile.",
        ) from error

    except AvatarMediaAnalysisError as error:
        if uploaded_asset_id and not uploaded_asset_was_existing:
            storage_service.delete_asset(
                uploaded_asset_id
            )

        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=error.user_message,
        ) from error

    except AvatarEvidenceRepositoryError as error:
        if uploaded_asset_id and not uploaded_asset_was_existing:
            storage_service.delete_asset(
                uploaded_asset_id
            )

        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "We could not securely save this avatar source. "
                "Please try again."
            ),
        ) from error

    except HTTPException:
        raise

    except Exception as error:
        if uploaded_asset_id and not uploaded_asset_was_existing:
            storage_service.delete_asset(
                uploaded_asset_id
            )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "We could not securely upload this avatar source. "
                "Please try again."
            ),
        ) from error


@router.post(
    "/sign",
    response_model=AvatarMediaSignResponse,
)
def sign_avatar_media(
    request: Request,
    body: AvatarMediaSignRequest,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarMediaSignResponse:
    try:
        service = (
            AvatarMediaStorageService()
        )

        request_base_url = str(
            request.base_url
        ).rstrip("/")

        metadata = service.get_metadata(
            body.asset_id
        )
        require_profile_access(
            principal=principal,
            profile_id=metadata.profile_id,
        )

        return service.sign_download_url(
            asset_id=body.asset_id,
            base_url=request_base_url,
            expires_in_seconds=(
                body.expires_in_seconds
                or 900
            ),
        )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "We could not prepare this avatar source. "
                "Please try again."
            ),
        ) from error



@router.get(
    "/storage/health",
    response_model=AvatarMediaStorageHealthResponse,
)
def get_avatar_media_storage_health(
) -> AvatarMediaStorageHealthResponse:
    try:
        service = (
            AvatarMediaStorageService()
        )

        return service.storage_health()

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar media storage is temporarily unavailable."
            ),
        ) from error


@router.get(
    "/assets/{asset_id}/metadata",
    response_model=AvatarMediaMetadata,
)
def get_avatar_media_metadata(
    asset_id: str,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarMediaMetadata:
    try:
        service = (
            AvatarMediaStorageService()
        )

        metadata = service.get_metadata(
            asset_id
        )
        require_profile_access(
            principal=principal,
            profile_id=metadata.profile_id,
        )
        return metadata

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "This avatar source no longer exists."
            ),
        ) from error


@router.get(
    "/profiles/{profile_id}/assets",
    response_model=AvatarMediaListResponse,
)
def list_avatar_media(
    profile_id: str,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> AvatarMediaListResponse:
    try:
        normalized_profile_id = str(
            UUID(profile_id)
        )

        require_profile_access(
            principal=principal,
            profile_id=normalized_profile_id,
        )

        service = (
            AvatarMediaStorageService()
        )

        return service.list_profile_assets(
            normalized_profile_id
        )

    except ValueError as error:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "profile_id must be a valid UUID."
            ),
        ) from error

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Avatar sources are temporarily unavailable."
            ),
        ) from error


@router.delete(
    "/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
def delete_avatar_media(
    asset_id: str,
    profile_id: str,
    principal: AuthenticatedSessionPrincipal = Depends(
        require_authenticated_principal
    ),
) -> Response:
    try:
        normalized_profile_id = str(UUID(profile_id))
        require_profile_access(
            principal=principal,
            profile_id=normalized_profile_id,
        )

        service = AvatarMediaStorageService()
        metadata = service.get_metadata(asset_id)
        if metadata.profile_id != normalized_profile_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="This media no longer exists.",
            )

        if metadata.asset_type not in {
            "memory_image",
            "memory_video",
        }:
            (
                AvatarMediaEvidenceBridgeService()
                .archive_uploaded_media_if_present(
                    asset_id=asset_id,
                    profile_id=normalized_profile_id,
                )
            )

        service.delete_asset(asset_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except HTTPException:
        raise
    except AvatarMediaEvidenceBridgeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This avatar source could not be deleted securely.",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="profile_id must be a valid UUID.",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This media no longer exists.",
        ) from error

@public_router.get(
    "/public/assets/{asset_id}"
)
def download_avatar_media(
    asset_id: str,
    expires: int,
    signature: str,
):
    try:
        service = (
            AvatarMediaStorageService()
        )

        metadata = (
            service
            .verify_download_signature(
                asset_id=asset_id,
                expires=expires,
                signature=signature,
            )
        )

        return FileResponse(
            path=metadata.storage_path,
            media_type=(
                metadata.content_type
            ),
            filename=metadata.filename,
        )

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
            ),
            detail=(
                "This avatar source is no longer available."
            ),
        ) from error
