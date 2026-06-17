from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

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
from app.services.avatar_face_analysis_service import (
    AvatarFaceAnalysisService,
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
) -> AvatarMediaUploadResponse:
    storage_service = (
        AvatarMediaStorageService()
    )

    uploaded_asset_id: Optional[str] = None

    try:
        normalized_profile_id = str(
            UUID(profile_id)
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

        if normalized_asset_type in {
            "image",
            "reference",
        }:
            face_service = (
                AvatarFaceAnalysisService()
            )

            face_analysis = (
                face_service.analyze(
                    content_type=(
                        response.content_type
                    ),
                    size_bytes=(
                        response.size_bytes
                    ),
                )
            )

            response.face_analysis = (
                face_analysis
            )

            analysis = (
                bridge
                .analysis_from_face_result(
                    face_analysis
                )
            )
        else:
            response.face_analysis = None

            analysis = (
                bridge
                .safe_registration_analysis(
                    asset_type=(
                        normalized_asset_type
                    )
                )
            )

        bridge.persist_uploaded_media(
            metadata=metadata,
            analysis=analysis,
        )

        return response

    except ValueError as error:
        if uploaded_asset_id:
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
        if uploaded_asset_id:
            storage_service.delete_asset(
                uploaded_asset_id
            )

        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(error),
        ) from error

    except AvatarEvidenceRepositoryError as error:
        if uploaded_asset_id:
            storage_service.delete_asset(
                uploaded_asset_id
            )

        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Avatar evidence persistence "
                f"failed: {error}"
            ),
        ) from error

    except Exception as error:
        if uploaded_asset_id:
            storage_service.delete_asset(
                uploaded_asset_id
            )

        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Avatar media upload failed: "
                f"{error}"
            ),
        ) from error


@router.post(
    "/sign",
    response_model=AvatarMediaSignResponse,
)
def sign_avatar_media(
    request: Request,
    body: AvatarMediaSignRequest,
) -> AvatarMediaSignResponse:
    try:
        service = (
            AvatarMediaStorageService()
        )

        request_base_url = str(
            request.base_url
        ).rstrip("/")

        return service.sign_download_url(
            asset_id=body.asset_id,
            base_url=request_base_url,
            expires_in_seconds=(
                body.expires_in_seconds
                or 900
            ),
        )

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Avatar media signing failed: "
                f"{error}"
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
                "Avatar media storage is "
                f"unavailable: {error}"
            ),
        ) from error


@router.get(
    "/assets/{asset_id}/metadata",
    response_model=AvatarMediaMetadata,
)
def get_avatar_media_metadata(
    asset_id: str,
) -> AvatarMediaMetadata:
    try:
        service = (
            AvatarMediaStorageService()
        )

        return service.get_metadata(
            asset_id
        )

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
            ),
            detail=(
                "Avatar media metadata failed: "
                f"{error}"
            ),
        ) from error


@router.get(
    "/profiles/{profile_id}/assets",
    response_model=AvatarMediaListResponse,
)
def list_avatar_media(
    profile_id: str,
) -> AvatarMediaListResponse:
    try:
        normalized_profile_id = str(
            UUID(profile_id)
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

    except Exception as error:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Avatar media listing failed: "
                f"{error}"
            ),
        ) from error


@public_router.get(
    "/assets/{asset_id}"
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
                "Avatar media download denied: "
                f"{error}"
            ),
        ) from error
