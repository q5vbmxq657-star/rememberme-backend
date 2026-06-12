import os
import secrets

from fastapi import Header, HTTPException, status


async def require_client_key(
    x_rememberme_client_key: str | None = Header(default=None),
) -> None:
    expected_key = os.getenv("REMEMBERME_CLIENT_API_KEY")

    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Client API key is not configured.",
        )

    if not x_rememberme_client_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing client API key.",
        )

    if not secrets.compare_digest(
        x_rememberme_client_key,
        expected_key,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid client API key.",
        )
