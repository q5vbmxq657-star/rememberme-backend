from fastapi import APIRouter, HTTPException
from app.services.system_health_service import SystemHealthService
from app.services.ai_orchestration_service import AIOrchestrationService

router = APIRouter()


@router.get("/health/deep")
def deep_health():
    try:
        service = SystemHealthService()
        return service.check()
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Deep health check failed: {str(error)}"
        )


@router.get("/ai/routes")
def ai_routes():
    try:
        service = AIOrchestrationService()
        return service.diagnostics()
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"AI route diagnostics failed: {str(error)}"
        )
