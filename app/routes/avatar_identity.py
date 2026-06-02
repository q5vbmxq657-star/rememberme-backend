from fastapi import APIRouter, HTTPException

from app.schemas.avatar_identity import (
    AvatarIdentityBlueprintRequest,
    AvatarIdentityBlueprintResponse,
)
from app.services.avatar_identity_blueprint_service import AvatarIdentityBlueprintService

router = APIRouter()


@router.post("/blueprint", response_model=AvatarIdentityBlueprintResponse)
def build_avatar_identity_blueprint(request: AvatarIdentityBlueprintRequest):
    try:
        service = AvatarIdentityBlueprintService()
        return service.build(request)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Avatar identity blueprint failed: {str(error)}"
        )
