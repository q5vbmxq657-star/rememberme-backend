from datetime import datetime, timezone
from uuid import uuid4

from app.models.avatar_evidence_asset import (
    AvatarEvidenceAsset,
)


def _asset(
    **overrides,
) -> AvatarEvidenceAsset:
    values = {
        "asset_id": uuid4(),
        "profile_id": uuid4(),
        "asset_type": "image",
        "evidence_kind": "identity_photo",
        "title": "Portrait",
        "filename": "portrait.jpg",
        "content_type": "image/jpeg",
        "size_bytes": 1000,
        "storage_backend": "local_private",
        "storage_key": "profile/portrait.jpg",
        "storage_path": "/tmp/portrait.jpg",
        "processing_status": "ready",
        "selection_status": "primary",
        "is_included_in_avatar": True,
        "included_in_avatar_at": datetime.now(
            timezone.utc
        ),
        "archived_at": None,
        "duration_seconds": None,
        "quality_score": 0.90,
        "has_face": True,
        "has_frontal_face": True,
        "has_clear_lighting": True,
        "has_voice": False,
        "voice_usable": False,
        "motion_usable": False,
        "emotional_presence_score": 0.80,
        "identity_consistency_score": 0.88,
        "motion_quality_score": 0.0,
        "expression_range_score": 0.0,
        "lip_visibility_score": 0.0,
        "head_pose_stability_score": 0.0,
        "recommended_for_avatar": True,
        "rejection_code": None,
        "rejection_reason": None,
        "analysis_version": "test-v1",
        "analysis_metadata": {},
        "source_metadata": {},
        "created_at": datetime.now(
            timezone.utc
        ),
        "updated_at": datetime.now(
            timezone.utc
        ),
    }

    values.update(overrides)

    return AvatarEvidenceAsset(**values)


def test_ready_selected_identity_is_active():
    assert _asset().is_active_avatar_evidence


def test_unselected_asset_is_not_active():
    asset = _asset(
        is_included_in_avatar=False,
        included_in_avatar_at=None,
        selection_status="not_selected",
    )

    assert not asset.is_active_avatar_evidence


def test_rejected_asset_is_not_quality_eligible():
    asset = _asset(
        rejection_reason="Rejected",
    )

    assert not asset.is_quality_eligible
    assert not asset.is_active_avatar_evidence


def test_low_quality_asset_is_not_active():
    asset = _asset(
        quality_score=0.71,
    )

    assert not asset.is_quality_eligible
    assert not asset.is_active_avatar_evidence


def test_archived_asset_is_not_active():
    asset = _asset(
        archived_at=datetime.now(
            timezone.utc
        ),
        processing_status="archived",
    )

    assert asset.is_archived
    assert not asset.is_active_avatar_evidence


def test_generated_preview_is_not_training_source():
    asset = _asset(
        evidence_kind="generated_preview",
        asset_type="generated_preview",
    )

    assert not asset.is_training_source
    assert not asset.is_active_avatar_evidence


def test_primary_selection_has_higher_priority():
    updated_at = datetime.now(
        timezone.utc
    )

    primary = _asset(
        selection_status="primary",
        quality_score=0.80,
        updated_at=updated_at,
    )

    selected = _asset(
        selection_status="selected",
        quality_score=0.99,
        updated_at=updated_at,
    )

    assert (
        primary.deterministic_priority
        >
        selected.deterministic_priority
    )


def test_priority_handles_missing_timestamp_safely():
    asset_without_timestamp = _asset(
        updated_at=None,
    )

    asset_with_timestamp = _asset(
        updated_at=datetime.now(
            timezone.utc
        ),
    )

    result = sorted(
        [
            asset_without_timestamp,
            asset_with_timestamp,
        ],
        key=lambda item: item.deterministic_priority,
    )

    assert len(result) == 2


def test_primary_beats_selected_even_without_timestamp():
    primary = _asset(
        selection_status="primary",
        updated_at=None,
        quality_score=0.80,
    )

    selected = _asset(
        selection_status="selected",
        updated_at=datetime.now(
            timezone.utc
        ),
        quality_score=0.99,
    )

    assert (
        primary.deterministic_priority
        >
        selected.deterministic_priority
    )
