import asyncio
import hmac
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request

from app.services.avatar_provider_service import avatar_provider_service, AvatarProviderStatusUnavailableError


router = APIRouter(
    prefix="/v1/provider-webhooks/tavus",
    tags=["provider-webhooks"],
)


def _verify_tavus_webhook_secret(
    *,
    query_secret: Optional[str],
    tavus_secret_header: Optional[str],
    rememberme_secret_header: Optional[str],
) -> None:
    expected_secret = os.getenv("TAVUS_WEBHOOK_SECRET")

    if not expected_secret:
        raise HTTPException(
            status_code=503,
            detail="TAVUS_WEBHOOK_SECRET is not configured.",
        )

    supplied_secret = (
        rememberme_secret_header
        or tavus_secret_header
        or query_secret
    )

    if not supplied_secret:
        raise HTTPException(
            status_code=401,
            detail="Missing Tavus webhook secret.",
        )

    if not hmac.compare_digest(
        supplied_secret.encode('utf-8'),
        expected_secret.encode('utf-8'),
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Tavus webhook secret.",
        )


@router.post("")
async def receive_tavus_webhook(
    request: Request,
    secret: Optional[str] = Query(default=None),
    x_tavus_webhook_secret: Optional[str] = Header(default=None),
    x_rememberme_webhook_secret: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    # FastAPI has bound the parameter already; do not retain it in access-log URLs.
    request.scope['query_string'] = b''
    _verify_tavus_webhook_secret(
        query_secret=secret,
        tavus_secret_header=x_tavus_webhook_secret,
        rememberme_secret_header=x_rememberme_webhook_secret,
    )

    try:
        payload = await request.json()
    except ValueError as error:
        raise HTTPException(422, 'Invalid callback payload.') from error
    if not isinstance(payload, dict):
        raise HTTPException(422, 'Invalid callback payload.')
    try:
        state = await asyncio.to_thread(avatar_provider_service.apply_tavus_webhook, payload)
    except AvatarProviderStatusUnavailableError as error:
        raise HTTPException(503, 'Avatar status could not be verified.', headers={'Retry-After': '12'}) from error

    if state.status == "failed" and state.error_message:
        return {
            "accepted": True,
            "external_job_id": state.external_job_id,
            "status": state.status,
            "error_message": state.error_message,
        }

    return {
        "accepted": True,
        "external_job_id": state.external_job_id,
        "external_avatar_id": state.external_avatar_id,
        "status": state.status,
        "preview_url": state.preview_url,
    }
