from pathlib import Path

from app.services.avatar_media_storage_service import (
    AvatarMediaStorageService,
)


def service(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AVATAR_MEDIA_STORAGE_ROOT",
        str(tmp_path),
    )

    return AvatarMediaStorageService()


def test_same_upload_id_resolves_same_asset_id(
    tmp_path,
    monkeypatch,
):
    storage = service(
        tmp_path,
        monkeypatch,
    )

    first = storage._resolve_asset_id(
        profile_id=(
            "3302091e-e8da-4d22-b4d3-"
            "b72c5c954c4c"
        ),
        upload_id="ios-upload-123",
    )

    second = storage._resolve_asset_id(
        profile_id=(
            "3302091e-e8da-4d22-b4d3-"
            "b72c5c954c4c"
        ),
        upload_id="ios-upload-123",
    )

    assert first == second


def test_different_profiles_do_not_share_id(
    tmp_path,
    monkeypatch,
):
    storage = service(
        tmp_path,
        monkeypatch,
    )

    first = storage._resolve_asset_id(
        profile_id=(
            "3302091e-e8da-4d22-b4d3-"
            "b72c5c954c4c"
        ),
        upload_id="same-upload",
    )

    second = storage._resolve_asset_id(
        profile_id=(
            "4302091e-e8da-4d22-b4d3-"
            "b72c5c954c4c"
        ),
        upload_id="same-upload",
    )

    assert first != second
