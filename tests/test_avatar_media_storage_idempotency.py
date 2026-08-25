from pathlib import Path

import pytest

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


def test_atomic_commit_reuses_identical_media(
    tmp_path: Path,
    monkeypatch,
):
    storage = service(tmp_path, monkeypatch)
    final = tmp_path / "final.bin"
    first = tmp_path / "first.upload"
    second = tmp_path / "second.upload"
    first.write_bytes(b"same-media")
    second.write_bytes(b"same-media")

    assert storage._commit_uploaded_file(
        temporary_file_path=first,
        final_file_path=final,
    ) is True
    assert storage._commit_uploaded_file(
        temporary_file_path=second,
        final_file_path=final,
    ) is False
    assert final.read_bytes() == b"same-media"


def test_atomic_commit_rejects_upload_id_collision(
    tmp_path: Path,
    monkeypatch,
):
    storage = service(tmp_path, monkeypatch)
    final = tmp_path / "final.bin"
    first = tmp_path / "first.upload"
    collision = tmp_path / "collision.upload"
    first.write_bytes(b"first-media")
    collision.write_bytes(b"different-media")

    assert storage._commit_uploaded_file(
        temporary_file_path=first,
        final_file_path=final,
    ) is True

    with pytest.raises(
        RuntimeError,
        match="different media",
    ):
        storage._commit_uploaded_file(
            temporary_file_path=collision,
            final_file_path=final,
        )

    assert final.read_bytes() == b"first-media"
