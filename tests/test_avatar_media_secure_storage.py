from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.avatar_media_storage_service import (
    AvatarMediaStorageConfigurationError,
    AvatarMediaStorageService,
)
from app.schemas.avatar_media import AvatarMediaMetadata


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
        volume_mount_path=(
            tmp_path
            if environment == "production"
            else None
        ),
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
    assert health.persistent_volume_configured is True


def test_production_rejects_ephemeral_storage(
    tmp_path: Path,
):
    with pytest.raises(
        AvatarMediaStorageConfigurationError,
        match="RAILWAY_VOLUME_MOUNT_PATH",
    ):
        AvatarMediaStorageService(
            storage_root=tmp_path,
            environment="production",
            public_base_url="https://api.rememberme.ai",
            signing_secret=SECURE_SECRET,
        )


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


@pytest.mark.parametrize(
    ("asset_type", "content_type"),
    [
        ("memory_image", "image/jpeg"),
        ("memory_video", "video/quicktime"),
    ],
)
def test_memory_gallery_media_types_are_supported(
    tmp_path: Path,
    asset_type: str,
    content_type: str,
):
    service = make_service(tmp_path)

    service._validate_asset_type(asset_type)
    service._validate_asset_type_matches_content_type(
        asset_type,
        content_type,
    )


def test_metadata_persists_a_portable_relative_storage_path(
    tmp_path: Path,
):
    service = make_service(tmp_path)
    profile_id = "profile-portable"
    asset_id = "9f4ef18b-6f87-4bda-940f-71c90740c913"
    filename = f"{asset_id}.jpg"
    media_path = tmp_path / profile_id / filename
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"image")

    service._write_metadata(
        AvatarMediaMetadata(
            asset_id=asset_id,
            profile_id=profile_id,
            asset_type="image",
            title="Portable portrait",
            filename=filename,
            content_type="image/jpeg",
            size_bytes=5,
            storage_path=str(media_path),
            created_at="2026-08-20T00:00:00+00:00",
        )
    )

    metadata_file = (
        tmp_path
        / profile_id
        / "metadata"
        / f"{asset_id}.json"
    )
    payload = json.loads(
        metadata_file.read_text(encoding="utf-8")
    )

    assert payload["storage_path"] == (
        f"{profile_id}/{filename}"
    )
    assert service.get_metadata(asset_id).storage_path == str(
        media_path.resolve()
    )


def test_legacy_absolute_metadata_rebinds_to_current_storage_root(
    tmp_path: Path,
):
    service = make_service(tmp_path)
    profile_id = "profile-relocated"
    asset_id = "3dd95762-39f1-4ee8-a6f4-68afb4d7ec83"
    filename = f"{asset_id}.jpg"
    profile_dir = tmp_path / profile_id
    metadata_dir = profile_dir / "metadata"
    metadata_dir.mkdir(parents=True)
    media_path = profile_dir / filename
    media_path.write_bytes(b"image")

    metadata_file = metadata_dir / f"{asset_id}.json"
    metadata_file.write_text(
        json.dumps(
            {
                "asset_id": asset_id,
                "profile_id": profile_id,
                "asset_type": "image",
                "title": "Relocated portrait",
                "filename": filename,
                "content_type": "image/jpeg",
                "size_bytes": 5,
                "storage_path": (
                    "/obsolete/backend/storage/avatar-media/"
                    f"{profile_id}/{filename}"
                ),
                "created_at": "2026-08-20T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    metadata = service.get_metadata(asset_id)

    assert metadata.storage_path == str(
        media_path.resolve()
    )
