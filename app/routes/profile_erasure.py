from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from app.security.user_auth import AuthenticatedSessionPrincipal, require_authenticated_principal
from app.services.profile_erasure_service import ProfileErasureService
from app.services.profile_erasure_access import ProfileErasureAccess, ErasureAccessDenied

router = APIRouter()


def get_erasure_service() -> ProfileErasureService:
    return ProfileErasureService()


@router.delete('/{profile_id}', status_code=204)
async def delete_profile(profile_id: UUID,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
    service: ProfileErasureService = Depends(get_erasure_service)) -> Response:
    try:
        request = await run_in_threadpool(
            ProfileErasureAccess(service.repository).authorize_request, principal, profile_id)
        await service._run(request)
        current = await run_in_threadpool(service.repository.get_profile_erasure_request,
                                         request_id=request['request_id'])
        if not current or current['status'] != 'completed':
            raise RuntimeError('Deletion remains pending.')
    except ErasureAccessDenied as error:
        raise HTTPException(404, 'Profile not found.') from error
    except Exception as error:
        raise HTTPException(503, 'Deletion is pending. Please try again.',
                            headers={'Retry-After': '300', 'Cache-Control': 'no-store'}) from error
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})
