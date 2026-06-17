from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

from app.models.avatar_evidence_asset import (
    AvatarEvidenceAsset,
)


def _asset(
    *,
    asset_id: UUID,
    profile_id: UUID,
    selection_status: str,
    included: bool,
    quality_score: float,
) -> AvatarEvidenceAsset:
    return AvatarEvidenceAsset(
        asset_id=asset_id,
        profile_id=profile_id,
        asset_type="image",
        evidence_kind="identity_photo",
        title="Test Portrait",
        filename="portrait.jpg",
        content_type="image/jpeg",
        size_bytes=1000,
        storage_backend="test",
        storage_key=str(asset_id),
        storage_path=None,
        processing_status="ready",
        selection_status=selection_status,
        is_included_in_avatar=included,
        included_in_avatar_at=None,
        archived_at=None,
        duration_seconds=None,
        quality_score=quality_score,
        has_face=True,
        has_frontal_face=True,
        has_clear_lighting=True,
        has_voice=False,
        voice_usable=False,
        motion_usable=False,
        emotional_presence_score=0.8,
        identity_consistency_score=0.9,
        motion_quality_score=0.0,
        expression_range_score=0.0,
        lip_visibility_score=0.0,
        head_pose_stability_score=0.0,
        recommended_for_avatar=True,
        rejection_code=None,
        rejection_reason=None,
        analysis_version="test-v1",
        analysis_metadata={},
        source_metadata={},
        created_at=None,
        updated_at=None,
    )


def test_primary_status_is_semantically_distinct_from_selected() -> None:
    profile_id = uuid4()

    selected_asset = _asset(
        asset_id=uuid4(),
        profile_id=profile_id,
        selection_status="selected",
        included=True,
        quality_score=0.95,
    )

    primary_asset = replace(
        selected_asset,
        asset_id=uuid4(),
        selection_status="primary",
        quality_score=0.90,
    )

    assert selected_asset.selection_status != "primary"
    assert primary_asset.selection_status == "primary"


def test_removed_asset_is_not_included() -> None:
    profile_id = uuid4()

    removed_asset = _asset(
        asset_id=uuid4(),
        profile_id=profile_id,
        selection_status="removed",
        included=False,
        quality_score=0.95,
    )

    assert removed_asset.selection_status == "removed"
    assert removed_asset.is_included_in_avatar is False
