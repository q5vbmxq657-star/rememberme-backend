from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.models.avatar_evidence_asset import (
    AvatarEvidenceAsset,
)
from app.models.digital_human_profile import (
    DigitalHumanProfile,
)
from app.schemas.avatar_generation_readiness import (
    AvatarGenerationReadinessRequest,
)
from app.services.avatar_generation_readiness_service import (
    AvatarGenerationReadinessService,
)


class FakeProfileRepository:
    def __init__(
        self,
        profile: DigitalHumanProfile,
    ) -> None:
        self.profile = profile
        self.updated_quality = None

    def require(
        self,
        profile_id: UUID,
    ) -> DigitalHumanProfile:
        assert profile_id == self.profile.profile_id
        return self.profile

    def update_quality(
        self,
        profile_id: UUID,
        *,
        quality_tier: str,
        quality_percentage: int,
        metadata=None,
    ) -> DigitalHumanProfile:
        assert profile_id == self.profile.profile_id

        self.updated_quality = {
            "quality_tier": quality_tier,
            "quality_percentage": quality_percentage,
            "metadata": metadata or {},
        }

        return self.profile


class FakeEvidenceRepository:
    def __init__(
        self,
        *,
        identity_assets=None,
        motion_assets=None,
        primary_identity=None,
        primary_motion=None,
    ) -> None:
        self.identity_assets = list(
            identity_assets or []
        )
        self.motion_assets = list(
            motion_assets or []
        )
        self.primary_identity = primary_identity
        self.primary_motion = primary_motion
        self.list_calls = []
        self.primary_calls = []

    def list_active_assets(
        self,
        profile_id: UUID,
        *,
        evidence_kind=None,
    ):
        self.list_calls.append(
            (
                profile_id,
                evidence_kind,
            )
        )

        if evidence_kind == "identity_photo":
            return list(self.identity_assets)

        if evidence_kind == "motion_video":
            return list(self.motion_assets)

        return (
            list(self.identity_assets)
            + list(self.motion_assets)
        )

    def resolve_primary(
        self,
        profile_id: UUID,
        evidence_kind: str,
    ):
        self.primary_calls.append(
            (
                profile_id,
                evidence_kind,
            )
        )

        if evidence_kind == "identity_photo":
            return self.primary_identity

        if evidence_kind == "motion_video":
            return self.primary_motion

        return None


class ExplodingLegacyIdentityService:
    def fuse(self, request):
        raise AssertionError(
            "Legacy identity fusion must not be called."
        )


class ExplodingLegacyMotionService:
    def assess(self, request):
        raise AssertionError(
            "Legacy motion readiness must not be called."
        )


def make_profile(
    *,
    profile_id: UUID | None = None,
    consent_verified: bool = True,
    avatar_status: str = "not_started",
    avatar_replica_id: str | None = None,
    avatar_provider: str | None = None,
) -> DigitalHumanProfile:
    now = datetime.now(
        timezone.utc
    )

    return DigitalHumanProfile(
        profile_id=profile_id or uuid4(),
        quality_tier="premium_presence",
        quality_percentage=25,
        avatar_provider=avatar_provider,
        avatar_replica_id=avatar_replica_id,
        avatar_persona_id=None,
        avatar_training_job_id=None,
        avatar_training_status=avatar_status,
        voice_provider="elevenlabs",
        voice_id="voice-id",
        voice_training_job_id=None,
        voice_training_status="ready",
        approved_portrait_url=None,
        consent_verified=consent_verified,
        training_version=1,
        runtime_verified_at=None,
        avatar_ready_at=None,
        voice_ready_at=now,
        last_error_code=None,
        last_error_message=None,
        metadata={},
        created_at=now,
        updated_at=now,
    )


def make_asset(
    *,
    profile_id: UUID,
    evidence_kind: str,
    selection_status: str = "primary",
    included: bool = True,
    processing_status: str = "ready",
    archived_at=None,
    quality_score: float = 0.90,
    rejection_reason=None,
    identity_consistency_score: float = 0.90,
    emotional_presence_score: float = 0.80,
    motion_quality_score: float = 0.90,
    expression_range_score: float = 0.85,
    lip_visibility_score: float = 0.88,
    head_pose_stability_score: float = 0.86,
    motion_usable: bool = True,
) -> AvatarEvidenceAsset:
    now = datetime.now(
        timezone.utc
    )

    asset_type = (
        "image"
        if evidence_kind == "identity_photo"
        else "video"
    )

    content_type = (
        "image/jpeg"
        if asset_type == "image"
        else "video/mp4"
    )

    return AvatarEvidenceAsset(
        asset_id=uuid4(),
        profile_id=profile_id,
        asset_type=asset_type,
        evidence_kind=evidence_kind,
        title="Evidence",
        filename=(
            "evidence.jpg"
            if asset_type == "image"
            else "evidence.mp4"
        ),
        content_type=content_type,
        size_bytes=1000,
        storage_backend="test",
        storage_key=str(uuid4()),
        storage_path=None,
        processing_status=processing_status,
        selection_status=selection_status,
        is_included_in_avatar=included,
        included_in_avatar_at=(
            now
            if included
            else None
        ),
        archived_at=archived_at,
        duration_seconds=(
            45.0
            if asset_type == "video"
            else None
        ),
        quality_score=quality_score,
        has_face=True,
        has_frontal_face=True,
        has_clear_lighting=True,
        has_voice=(
            asset_type == "video"
        ),
        voice_usable=False,
        motion_usable=motion_usable,
        emotional_presence_score=(
            emotional_presence_score
        ),
        identity_consistency_score=(
            identity_consistency_score
        ),
        motion_quality_score=(
            motion_quality_score
        ),
        expression_range_score=(
            expression_range_score
        ),
        lip_visibility_score=(
            lip_visibility_score
        ),
        head_pose_stability_score=(
            head_pose_stability_score
        ),
        recommended_for_avatar=True,
        rejection_code=(
            "rejected"
            if rejection_reason
            else None
        ),
        rejection_reason=rejection_reason,
        analysis_version="test-v1",
        analysis_metadata={},
        source_metadata={},
        created_at=now,
        updated_at=now,
    )


def make_service(
    *,
    profile: DigitalHumanProfile,
    evidence_repository: FakeEvidenceRepository,
):
    profile_repository = FakeProfileRepository(
        profile
    )

    service = AvatarGenerationReadinessService(
        repository=profile_repository,
        evidence_repository=evidence_repository,
        identity_fusion_service=(
            ExplodingLegacyIdentityService()
        ),
        motion_service=(
            ExplodingLegacyMotionService()
        ),
    )

    return service, profile_repository


def test_readiness_uses_only_persistent_evidence():
    profile = make_profile()

    identity = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="identity_photo",
    )

    motion = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="motion_video",
    )

    evidence_repository = FakeEvidenceRepository(
        identity_assets=[identity],
        motion_assets=[motion],
        primary_identity=identity,
        primary_motion=motion,
    )

    service, repository = make_service(
        profile=profile,
        evidence_repository=evidence_repository,
    )

    response = service.assess(
        AvatarGenerationReadinessRequest(
            profile_id=profile.profile_id
        )
    )

    assert response.identity_score > 0.88
    assert response.motion_score > 0.80

    assert response.primary_identity_asset_id == str(
        identity.asset_id
    )

    assert response.primary_motion_asset_id == str(
        motion.asset_id
    )

    assert repository.updated_quality is not None

    assert (
        profile.profile_id,
        "identity_photo",
    ) in evidence_repository.list_calls

    assert (
        profile.profile_id,
        "motion_video",
    ) in evidence_repository.list_calls


def test_runtime_avatar_cannot_raise_visual_scores():
    profile = make_profile(
        avatar_status="ready",
        avatar_replica_id="existing-replica",
        avatar_provider="tavus",
    )

    evidence_repository = FakeEvidenceRepository()

    service, _ = make_service(
        profile=profile,
        evidence_repository=evidence_repository,
    )

    response = service.assess(
        AvatarGenerationReadinessRequest(
            profile_id=profile.profile_id
        )
    )

    assert response.identity_score == 0.0
    assert response.motion_score == 0.0
    assert response.primary_identity_asset_id is None
    assert response.primary_motion_asset_id is None
    assert response.quality_tier == "premium_presence"


def test_non_active_assets_do_not_affect_readiness():
    profile = make_profile()

    removed_identity = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="identity_photo",
        selection_status="removed",
        included=False,
    )

    archived_motion = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="motion_video",
        processing_status="archived",
        included=False,
        archived_at=datetime.now(
            timezone.utc
        ),
    )

    evidence_repository = FakeEvidenceRepository(
        identity_assets=[],
        motion_assets=[],
        primary_identity=None,
        primary_motion=None,
    )

    service, _ = make_service(
        profile=profile,
        evidence_repository=evidence_repository,
    )

    response = service.assess(
        AvatarGenerationReadinessRequest(
            profile_id=profile.profile_id
        )
    )

    assert removed_identity.is_active_avatar_evidence is False
    assert archived_motion.is_active_avatar_evidence is False
    assert response.identity_score == 0.0
    assert response.motion_score == 0.0


def test_low_quality_or_rejected_assets_do_not_raise_scores():
    profile = make_profile()

    low_quality = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="identity_photo",
        quality_score=0.71,
    )

    rejected = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="motion_video",
        rejection_reason="Unusable motion",
    )

    assert low_quality.is_active_avatar_evidence is False
    assert rejected.is_active_avatar_evidence is False

    evidence_repository = FakeEvidenceRepository(
        identity_assets=[],
        motion_assets=[],
    )

    service, _ = make_service(
        profile=profile,
        evidence_repository=evidence_repository,
    )

    response = service.assess(
        AvatarGenerationReadinessRequest(
            profile_id=profile.profile_id
        )
    )

    assert response.identity_score == 0.0
    assert response.motion_score == 0.0


def test_primary_ids_require_real_primary_assets():
    profile = make_profile()

    selected_identity = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="identity_photo",
        selection_status="selected",
    )

    selected_motion = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="motion_video",
        selection_status="selected",
    )

    evidence_repository = FakeEvidenceRepository(
        identity_assets=[selected_identity],
        motion_assets=[selected_motion],
        primary_identity=None,
        primary_motion=None,
    )

    service, _ = make_service(
        profile=profile,
        evidence_repository=evidence_repository,
    )

    response = service.assess(
        AvatarGenerationReadinessRequest(
            profile_id=profile.profile_id
        )
    )

    assert response.identity_score > 0.0
    assert response.motion_score > 0.0
    assert response.primary_identity_asset_id is None
    assert response.primary_motion_asset_id is None


def test_best_active_asset_determines_score():
    profile = make_profile()

    weaker = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="identity_photo",
        quality_score=0.73,
        identity_consistency_score=0.73,
        emotional_presence_score=0.50,
    )

    stronger = make_asset(
        profile_id=profile.profile_id,
        evidence_kind="identity_photo",
        quality_score=0.96,
        identity_consistency_score=0.95,
        emotional_presence_score=0.90,
    )

    evidence_repository = FakeEvidenceRepository(
        identity_assets=[
            weaker,
            stronger,
        ],
        primary_identity=stronger,
    )

    service, _ = make_service(
        profile=profile,
        evidence_repository=evidence_repository,
    )

    expected = service._identity_asset_score(
        stronger
    )

    response = service.assess(
        AvatarGenerationReadinessRequest(
            profile_id=profile.profile_id
        )
    )

    assert response.identity_score == round(
        expected,
        3,
    )
