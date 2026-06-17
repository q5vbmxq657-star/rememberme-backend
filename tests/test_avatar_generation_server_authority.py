from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models.digital_human_profile import (
    DigitalHumanProfile,
)
from app.schemas.avatar_generation_readiness import (
    AvatarGenerationReadinessRequest,
)
from app.services.avatar_generation_readiness_service import (
    AvatarGenerationReadinessService,
)


class FakeRepository:
    def __init__(
        self,
        profile: DigitalHumanProfile,
    ) -> None:
        self.profile = profile
        self.updated_quality = None

    def require(self, profile_id):
        assert profile_id == self.profile.profile_id
        return self.profile

    def update_quality(
        self,
        profile_id,
        *,
        quality_tier,
        quality_percentage,
        metadata=None,
    ):
        assert profile_id == self.profile.profile_id

        self.updated_quality = {
            "quality_tier": quality_tier,
            "quality_percentage": (
                quality_percentage
            ),
            "metadata": metadata or {},
        }

        return self.profile


class FakeIdentityFusionService:
    def __init__(
        self,
        score: float,
    ) -> None:
        self.score = score

    def fuse(self, request):
        return SimpleNamespace(
            identity_stability_score=self.score,
            primary_reference_asset_id=None,
        )


class FakeMotionService:
    def __init__(
        self,
        score: float,
    ) -> None:
        self.score = score

    def assess(self, request):
        return SimpleNamespace(
            talking_portrait_readiness_score=(
                self.score
            ),
            recommended_primary_motion_asset_id=(
                None
            ),
        )



class FakeEvidenceRepository:
    """
    Explicit in-memory Evidence dependency for isolated readiness tests.

    Production code continues to require the real PostgreSQL-backed
    AvatarEvidenceRepository. Only unit tests use this deterministic fake.
    """

    def __init__(
        self,
        *,
        identity_assets=None,
        motion_assets=None,
        primary_identity=None,
        primary_motion=None,
    ):
        self.identity_assets = list(
            identity_assets or []
        )
        self.motion_assets = list(
            motion_assets or []
        )
        self.primary_identity = primary_identity
        self.primary_motion = primary_motion

    def list_active_assets(
        self,
        profile_id,
        *,
        evidence_kind=None,
    ):
        if evidence_kind == "identity_photo":
            return list(
                self.identity_assets
            )

        if evidence_kind == "motion_video":
            return list(
                self.motion_assets
            )

        return (
            list(self.identity_assets)
            + list(self.motion_assets)
        )

    def resolve_primary(
        self,
        profile_id,
        evidence_kind,
    ):
        if evidence_kind == "identity_photo":
            return self.primary_identity

        if evidence_kind == "motion_video":
            return self.primary_motion

        return None


def make_profile(
    *,
    consent_verified: bool,
    voice_status: str = "not_started",
    voice_id=None,
    voice_provider=None,
    avatar_status: str = "not_started",
    avatar_replica_id=None,
    avatar_provider=None,
    metadata=None,
):
    now = datetime.now(timezone.utc)

    return DigitalHumanProfile(
        profile_id=uuid4(),
        quality_tier="premium_presence",
        quality_percentage=25,
        avatar_provider=avatar_provider,
        avatar_replica_id=(
            avatar_replica_id
        ),
        avatar_persona_id=None,
        avatar_training_job_id=None,
        avatar_training_status=(
            avatar_status
        ),
        voice_provider=voice_provider,
        voice_id=voice_id,
        voice_training_job_id=None,
        voice_training_status=voice_status,
        approved_portrait_url=None,
        consent_verified=consent_verified,
        training_version=1,
        runtime_verified_at=None,
        avatar_ready_at=None,
        voice_ready_at=(
            now
            if voice_status == "ready"
            else None
        ),
        last_error_code=None,
        last_error_message=None,
        metadata=metadata or {},
        created_at=now,
        updated_at=now,
    )


def service_for(
    profile,
    *,
    identity_score=0.0,
    motion_score=0.0,
    evidence_repository=None,
):
    repository = FakeRepository(profile)

    service = AvatarGenerationReadinessService(
        repository=repository,
        evidence_repository=(
            evidence_repository
            or FakeEvidenceRepository()
        ),
        identity_fusion_service=(
            FakeIdentityFusionService(
                identity_score
            )
        ),
        motion_service=(
            FakeMotionService(
                motion_score
            )
        ),
    )

    return service, repository


def test_legacy_client_scores_are_ignored():
    profile = make_profile(
        consent_verified=False,
        voice_status="not_started",
    )

    service, _ = service_for(profile)

    request = (
        AvatarGenerationReadinessRequest
        .model_validate(
            {
                "profile_id": str(
                    profile.profile_id
                ),
                "consent_verified": True,
                "voice_identity_score": 1.0,
                "behavioral_persona_score": 1.0,
            }
        )
    )

    response = service.assess(request)

    assert response.consent_score == 0.0
    assert response.voice_score == 0.0
    assert response.persona_score == 0.0
    assert response.quality_tier == "blocked"
    assert response.presentation.voice_mode == (
        "silent"
    )


def test_ready_persistent_voice_is_enabled():
    profile = make_profile(
        consent_verified=True,
        voice_status="ready",
        voice_id="server-voice-id",
        voice_provider="elevenlabs",
    )

    service, repository = service_for(
        profile
    )

    response = service.assess(
        AvatarGenerationReadinessRequest(
            profile_id=profile.profile_id
        )
    )

    assert response.voice_score == 1.0

    personalized_voice = next(
        capability
        for capability
        in response.capabilities
        if capability.capability
        == "personalized_voice"
    )

    assert personalized_voice.status == (
        "enabled"
    )

    assert response.presentation.voice_mode == (
        "personalized_when_ready"
    )

    assert repository.updated_quality is not None


def test_ready_runtime_avatar_cannot_raise_visual_scores():
    profile = make_profile(
        consent_verified=True,
        voice_status="ready",
        voice_id="server-voice-id",
        voice_provider="elevenlabs",
        avatar_status="ready",
        avatar_replica_id="replica-id",
        avatar_provider="tavus",
        metadata={
            "persona": {
                "confidence_score": 0.75
            }
        },
    )

    service, _ = service_for(
        profile,
        identity_score=0.0,
        motion_score=0.0,
    )

    response = service.assess(
        AvatarGenerationReadinessRequest(
            profile_id=profile.profile_id
        )
    )

    assert response.identity_score == 0.0
    assert response.motion_score == 0.0

    assert (
        response.primary_identity_asset_id
        is None
    )

    assert (
        response.primary_motion_asset_id
        is None
    )

    assert response.voice_score == 1.0
    assert response.persona_score == 0.75

    assert response.quality_tier == (
        "premium_presence"
    )




def test_persona_score_comes_from_server_metadata():
    profile = make_profile(
        consent_verified=True,
        metadata={
            "persona_confidence_score": 0.63
        },
    )

    service, _ = service_for(profile)

    response = service.assess(
        AvatarGenerationReadinessRequest(
            profile_id=profile.profile_id
        )
    )

    assert response.persona_score == 0.63
