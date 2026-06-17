from app.services.avatar_generation_readiness_service import (
    AvatarGenerationReadinessService,
)


def _resolve(
    *,
    consent: float = 1.0,
    identity: float,
    motion: float,
    voice: float,
    persona: float,
):
    service = AvatarGenerationReadinessService()

    overall = service._overall_score(
        identity_score=identity,
        motion_score=motion,
        voice_score=voice,
        persona_score=persona,
        consent_score=consent,
    )

    return service._resolve_quality_tier(
        consent_score=consent,
        identity_score=identity,
        motion_score=motion,
        voice_score=voice,
        persona_score=persona,
        overall_score=overall,
    )


def test_signature_live_requires_complete_evidence():
    result = _resolve(
        identity=0.94,
        motion=0.90,
        voice=0.91,
        persona=0.82,
    )

    assert result.quality_tier == "signature_live"
    assert result.quality_percentage == 100
    assert result.presentation_mode == "realtime_replica"
    assert result.voice_mode == "personalized"


def test_expressive_live_handles_good_but_imperfect_material():
    result = _resolve(
        identity=0.84,
        motion=0.74,
        voice=0.70,
        persona=0.62,
    )

    assert result.quality_tier == "expressive_live"
    assert result.presentation_mode == "controlled_talking_avatar"


def test_guided_live_limits_motion_for_mixed_material():
    result = _resolve(
        identity=0.76,
        motion=0.52,
        voice=0.58,
        persona=0.55,
    )

    assert result.quality_tier == "guided_live"
    assert result.presentation_mode == "guided_talking_avatar"
    assert result.gesture_intensity == "minimal"


def test_cinematic_portrait_avoids_uncanny_animation():
    result = _resolve(
        identity=0.64,
        motion=0.20,
        voice=0.42,
        persona=0.42,
    )

    assert result.quality_tier == "cinematic_portrait"
    assert result.presentation_mode == "cinematic_portrait"
    assert result.gesture_intensity == "none"
    assert result.facial_expression_intensity == "none"


def test_premium_presence_remains_available_with_weak_visual_evidence():
    result = _resolve(
        identity=0.30,
        motion=0.10,
        voice=0.20,
        persona=0.52,
    )

    assert result.quality_tier == "premium_presence"
    assert result.presentation_mode == "abstract_presence"
    assert result.voice_mode == "warm_default"


def test_missing_consent_blocks_generation():
    result = _resolve(
        consent=0.0,
        identity=1.0,
        motion=1.0,
        voice=1.0,
        persona=1.0,
    )

    assert result.quality_tier == "blocked"
    assert result.presentation_mode == "blocked"
    assert result.voice_mode == "silent"
