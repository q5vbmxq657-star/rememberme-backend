from __future__ import annotations

from pathlib import Path

import pytest

from app.services.avatar_media_storage_service import (
    AvatarMediaStorageConfigurationError,
    AvatarMediaStorageService,
)


SECURE_SECRET = (
    "0123456789abcdef"
    "0123456789abcdef"
)


def make_service(
    tmp_path: Path,
    *,
    environment: str = "development",
    public_base_url: str | None = None,
    signing_secret: str = SECURE_SECRET,
) -> AvatarMediaStorageService:
    return AvatarMediaStorageService(
        storage_root=tmp_path,
        environment=environment,
        public_base_url=public_base_url,
        signing_secret=signing_secret,
    )


def test_public_base_url_is_authoritative(
    tmp_path: Path,
):
    service = make_service(
        tmp_path,
        public_base_url=(
            "https://api.rememberme.ai/"
        ),
    )

    resolved = (
        service.resolve_external_base_url(
            request_base_url=(
                "http://attacker.invalid"
            )
        )
    )

    assert resolved == (
        "https://api.rememberme.ai"
    )


def test_public_base_url_requires_https(
    tmp_path: Path,
):
    with pytest.raises(
        AvatarMediaStorageConfigurationError
    ):
        make_service(
            tmp_path,
            public_base_url=(
                "http://api.rememberme.ai"
            ),
        )


def test_local_http_fallback_is_allowed(
    tmp_path: Path,
):
    service = make_service(
        tmp_path
    )

    resolved = (
        service.resolve_external_base_url(
            request_base_url=(
                "http://localhost:8000/"
            )
        )
    )

    assert resolved == (
        "http://localhost:8000"
    )


def test_non_local_http_fallback_is_rejected(
    tmp_path: Path,
):
    service = make_service(
        tmp_path
    )

    with pytest.raises(
        AvatarMediaStorageConfigurationError
    ):
        service.resolve_external_base_url(
            request_base_url=(
                "http://public.example.com"
            )
        )


def test_production_requires_public_base_url(
    tmp_path: Path,
):
    with pytest.raises(
        AvatarMediaStorageConfigurationError
    ):
        make_service(
            tmp_path,
            environment="production",
            public_base_url=None,
        )


def test_production_rejects_development_secret(
    tmp_path: Path,
):
    with pytest.raises(
        AvatarMediaStorageConfigurationError
    ):
        make_service(
            tmp_path,
            environment="production",
            public_base_url=(
                "https://api.rememberme.ai"
            ),
            signing_secret=(
                AvatarMediaStorageService
                .development_signing_secret
            ),
        )


def test_storage_health_proves_writability(
    tmp_path: Path,
):
    service = make_service(
        tmp_path,
        public_base_url=(
            "https://api.rememberme.ai"
        ),
    )

    health = service.storage_health()

    assert health.storage_exists is True
    assert (
        health.storage_is_directory
        is True
    )
    assert health.storage_writable is True
    assert (
        health.public_base_url_configured
        is True
    )
    assert (
        health.secure_public_url
        is True
    )
    assert (
        health.signing_secret_secure
        is True
    )


def test_production_health_is_ready(
    tmp_path: Path,
):
    service = make_service(
        tmp_path,
        environment="production",
        public_base_url=(
            "https://api.rememberme.ai"
        ),
    )

    health = service.storage_health()

    assert health.status == "ready"
    assert health.production_ready is True


def test_delete_rejects_path_outside_root(
    tmp_path: Path,
):
    service = make_service(
        tmp_path
    )

    outside = (
        tmp_path.parent
        / "outside.jpg"
    )

    assert (
        service._is_within_storage_root(
            outside.resolve()
        )
        is False
    )
