from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse

from app.schemas.avatar_media import (
    AvatarMediaUploadResponse,
    AvatarMediaMetadata,
    AvatarMediaSignRequest,
    AvatarMediaSignResponse,
    AvatarMediaListResponse,
)

from app.services.avatar_media_storage_service import AvatarMediaStorageService
from app.services.avatar_face_analysis_service import AvatarFaceAnalysisService

router = APIRouter()


@router.post("/upload", response_model=AvatarMediaUploadResponse)
async def upload_avatar_media(
    request: Request,
    profile_id: str = Form(...),
    asset_type: str = Form(...),
    title: str = Form(...),
    file: UploadFile = File(...)
):
    try:
        service = AvatarMediaStorageService()
        face_service = AvatarFaceAnalysisService()

        base_url = str(request.base_url).rstrip("/")

        response = await service.upload(
            profile_id=profile_id,
            asset_type=asset_type,
            title=title,
            file=file,
            base_url=base_url
        )

        analysis = face_service.analyze(
            content_type=response.content_type,
            size_bytes=response.size_bytes
        )

        response.face_analysis = analysis

        return response

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar media upload failed: {str(error)}"
        )


@router.post("/sign", response_model=AvatarMediaSignResponse)
def sign_avatar_media(
    request: Request,
    body: AvatarMediaSignRequest
):
    try:
        service = AvatarMediaStorageService()

        base_url = str(request.base_url).rstrip("/")

        return service.sign_download_url(
            asset_id=body.asset_id,
            base_url=base_url,
            expires_in_seconds=body.expires_in_seconds or 900
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar media signing failed: {str(error)}"
        )


@router.get(
    "/assets/{asset_id}/metadata",
    response_model=AvatarMediaMetadata
)
def get_avatar_media_metadata(asset_id: str):
    try:
        service = AvatarMediaStorageService()
        return service.get_metadata(asset_id)

    except Exception as error:
        raise HTTPException(
            status_code=404,
            detail=f"Avatar media metadata failed: {str(error)}"
        )


@router.get(
    "/profiles/{profile_id}/assets",
    response_model=AvatarMediaListResponse
)
def list_avatar_media(profile_id: str):
    try:
        service = AvatarMediaStorageService()
        return service.list_profile_assets(profile_id)

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar media listing failed: {str(error)}"
        )


@router.get("/assets/{asset_id}")
def download_avatar_media(
    asset_id: str,
    expires: int,
    signature: str
):
    try:
        service = AvatarMediaStorageService()

        metadata = service.verify_download_signature(
            asset_id=asset_id,
            expires=expires,
            signature=signature
        )

        return FileResponse(
            path=metadata.storage_path,
            media_type=metadata.content_type,
            filename=metadata.filename
        )

    except Exception as error:
        raise HTTPException(
            status_code=403,
            detail=f"Avatar media download denied: {str(error)}"
        )
