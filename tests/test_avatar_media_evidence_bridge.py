from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.schemas.avatar_media import (
    AvatarMediaMetadata,
)
from app.services.avatar_media_evidence_bridge_service import (
    AvatarMediaEvidenceAnalysis,
    AvatarMediaEvidenceBridgeError,
    AvatarMediaEvidenceBridgeService,
)
from app.services.avatar_evidence_repository import (
    AvatarEvidenceNotFoundError,
)


class FakeEvidenceRepository:
    def __init__(self):
        self.calls = []
        self.archive_calls = []
        self.archive_is_missing = False

    def upsert_uploaded_asset(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        return kwargs

    def archive(self, **kwargs):
        self.archive_calls.append(kwargs)
        if self.archive_is_missing:
            raise AvatarEvidenceNotFoundError(
                "missing"
            )

        return kwargs


def metadata(
    *,
    asset_type: str,
) -> AvatarMediaMetadata:
    asset_id = uuid4()
    profile_id = uuid4()

    return AvatarMediaMetadata(
        asset_id=str(asset_id),
        profile_id=str(profile_id),
        asset_type=asset_type,
        title="Test asset",
        filename=f"{asset_id}.bin",
        content_type=(
            "image/jpeg"
            if asset_type
            in {"image", "reference"}
            else (
                "video/mp4"
                if asset_type
                in {
                    "video",
                    "training_sample",
                }
                else "audio/wav"
            )
        ),
        size_bytes=12345,
        storage_path=(
            f"/private/{profile_id}/"
            f"{asset_id}.bin"
        ),
        created_at=(
            "2026-06-17T12:00:00+00:00"
        ),
    )


@pytest.mark.parametrize(
    (
        "asset_type",
        "expected_kind",
    ),
    [
        (
            "image",
            "identity_photo",
        ),
        (
            "reference",
            "identity_photo",
        ),
        (
            "video",
            "motion_video",
        ),
        (
            "training_sample",
            "motion_video",
        ),
        (
            "voice",
            "voice_sample",
        ),
        (
            "audio",
            "voice_sample",
        ),
    ],
)
def test_asset_type_maps_to_canonical_kind(
    asset_type,
    expected_kind,
):
    assert (
        AvatarMediaEvidenceBridgeService
        .evidence_kind_for(
            asset_type
        )
        == expected_kind
    )


def test_generated_output_is_not_source_evidence():
    with pytest.raises(
        AvatarMediaEvidenceBridgeError
    ):
        (
            AvatarMediaEvidenceBridgeService
            .evidence_kind_for(
                "generated_preview"
            )
        )


def test_upload_persists_same_asset_identity():
    repository = (
        FakeEvidenceRepository()
    )

    service = (
        AvatarMediaEvidenceBridgeService(
            repository=repository
        )
    )

    item = metadata(
        asset_type="image"
    )

    service.persist_uploaded_media(
        metadata=item,
        analysis=(
            AvatarMediaEvidenceAnalysis(
                quality_score=0.84,
                has_face=True,
                has_frontal_face=True,
                has_clear_lighting=True,
                recommended_for_avatar=True,
            )
        ),
    )

    assert len(repository.calls) == 1

    call = repository.calls[0]

    assert call["asset_id"] == UUID(
        item.asset_id
    )

    assert call["profile_id"] == UUID(
        item.profile_id
    )

    assert (
        call["evidence_kind"]
        == "identity_photo"
    )

    assert (
        call["storage_key"]
        == (
            f"{item.profile_id}/"
            f"{item.filename}"
        )
    )


def test_upload_does_not_select_asset():
    repository = (
        FakeEvidenceRepository()
    )

    service = (
        AvatarMediaEvidenceBridgeService(
            repository=repository
        )
    )

    item = metadata(
        asset_type="video"
    )

    service.persist_uploaded_media(
        metadata=item,
        analysis=(
            service
            .safe_registration_analysis(
                asset_type="video"
            )
        ),
    )

    call = repository.calls[0]

    assert (
        "selection_status"
        not in call
    )

    assert (
        "is_included_in_avatar"
        not in call
    )

    assert call["motion_usable"] is False
    assert call["quality_score"] == 0.0


def test_audio_registration_is_not_voice_ready():
    analysis = (
        AvatarMediaEvidenceBridgeService
        .safe_registration_analysis(
            asset_type="audio"
        )
    )

    assert analysis.has_voice is True
    assert analysis.voice_usable is False
    assert analysis.quality_score == 0.0


def test_delete_archives_profile_bound_evidence():
    repository = FakeEvidenceRepository()
    service = AvatarMediaEvidenceBridgeService(
        repository=repository
    )
    asset_id = uuid4()
    profile_id = uuid4()

    service.archive_uploaded_media_if_present(
        asset_id=str(asset_id),
        profile_id=str(profile_id),
    )

    assert repository.archive_calls == [
        {
            "asset_id": asset_id,
            "profile_id": profile_id,
        }
    ]


def test_delete_is_idempotent_without_evidence():
    repository = FakeEvidenceRepository()
    repository.archive_is_missing = True
    service = AvatarMediaEvidenceBridgeService(
        repository=repository
    )

    service.archive_uploaded_media_if_present(
        asset_id=str(uuid4()),
        profile_id=str(uuid4()),
    )
