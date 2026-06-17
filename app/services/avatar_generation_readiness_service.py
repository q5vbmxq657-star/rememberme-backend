from __future__ import annotations

from dataclasses import dataclass
from typing import List

from app.schemas.avatar_generation_readiness import (
    AvatarCapabilityDecision,
    AvatarGenerationReadinessRequest,
    AvatarGenerationReadinessResponse,
    AvatarPresentationSpec,
)
from app.schemas.avatar_identity_fusion import AvatarIdentityFusionRequest
from app.schemas.avatar_motion import AvatarMotionReadinessRequest
from app.services.avatar_identity_fusion_service import AvatarIdentityFusionService
from app.services.avatar_motion_readiness_service import AvatarMotionReadinessService


@dataclass(frozen=True)
class _ResolvedAvatarTier:
    quality_tier: str
    quality_percentage: int
    presentation_mode: str
    voice_mode: str
    generation_status: str

    user_title: str
    user_message: str

    gesture_intensity: str
    facial_expression_intensity: str
    idle_motion: str


class AvatarGenerationReadinessService:
    """
    Canonical quality resolver for every avatar presentation.

    This service deliberately separates:

    1. Evidence quality
    2. Generation eligibility
    3. Runtime presentation quality

    Weak source material must never produce an unstable or uncanny avatar.
    Instead, the service resolves the highest trustworthy presentation tier.
    """

    def __init__(self) -> None:
        self.identity_fusion_service = AvatarIdentityFusionService()
        self.motion_service = AvatarMotionReadinessService()

    def assess(
        self,
        request: AvatarGenerationReadinessRequest,
    ) -> AvatarGenerationReadinessResponse:
        identity = self.identity_fusion_service.fuse(
            AvatarIdentityFusionRequest(
                profile_id=request.profile_id,
                min_quality_score=0.60,
            )
        )

        motion = self.motion_service.assess(
            AvatarMotionReadinessRequest(
                profile_id=request.profile_id,
            )
        )

        identity_score = self._clamp(
            identity.identity_stability_score
        )
        motion_score = self._clamp(
            motion.talking_portrait_readiness_score
        )
        voice_score = self._clamp(
            request.voice_identity_score
        )
        persona_score = self._clamp(
            request.behavioral_persona_score
        )
        consent_score = (
            1.0
            if request.consent_verified
            else 0.0
        )

        overall_score = self._overall_score(
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
            persona_score=persona_score,
            consent_score=consent_score,
        )

        resolved_tier = self._resolve_quality_tier(
            consent_score=consent_score,
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
            persona_score=persona_score,
            overall_score=overall_score,
        )

        capabilities = self._capabilities(
            tier=resolved_tier,
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
            persona_score=persona_score,
            consent_score=consent_score,
        )

        blockers = self._blockers(
            consent_score=consent_score,
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
        )

        presentation = AvatarPresentationSpec(
            quality_tier=resolved_tier.quality_tier,
            quality_percentage=resolved_tier.quality_percentage,
            presentation_mode=resolved_tier.presentation_mode,
            voice_mode=resolved_tier.voice_mode,
            framing="medium_close_up",
            camera_motion="locked",
            gesture_intensity=resolved_tier.gesture_intensity,
            facial_expression_intensity=(
                resolved_tier.facial_expression_intensity
            ),
            idle_motion=resolved_tier.idle_motion,
            show_full_face=True,
            show_upper_shoulders=True,
            allow_aggressive_close_up=False,
            user_title=resolved_tier.user_title,
            user_message=resolved_tier.user_message,
        )

        return AvatarGenerationReadinessResponse(
            profile_id=request.profile_id,
            generation_status=resolved_tier.generation_status,
            recommended_avatar_mode=(
                resolved_tier.presentation_mode
            ),
            quality_tier=resolved_tier.quality_tier,
            quality_percentage=(
                resolved_tier.quality_percentage
            ),
            overall_generation_score=overall_score,
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
            persona_score=persona_score,
            consent_score=consent_score,
            primary_identity_asset_id=(
                identity.primary_reference_asset_id
            ),
            primary_motion_asset_id=(
                motion.recommended_primary_motion_asset_id
            ),
            presentation=presentation,
            capabilities=capabilities,
            blockers=blockers,
            next_best_actions=self._next_best_actions(
                tier=resolved_tier,
                identity_score=identity_score,
                motion_score=motion_score,
                voice_score=voice_score,
                persona_score=persona_score,
            ),
        )

    def _resolve_quality_tier(
        self,
        consent_score: float,
        identity_score: float,
        motion_score: float,
        voice_score: float,
        persona_score: float,
        overall_score: float,
    ) -> _ResolvedAvatarTier:
        if consent_score <= 0.0:
            return _ResolvedAvatarTier(
                quality_tier="blocked",
                quality_percentage=0,
                presentation_mode="blocked",
                voice_mode="silent",
                generation_status="blocked",
                user_title="Permission needed",
                user_message=(
                    "Permission is required before creating this presence."
                ),
                gesture_intensity="none",
                facial_expression_intensity="none",
                idle_motion="none",
            )

        if (
            identity_score >= 0.88
            and motion_score >= 0.82
            and voice_score >= 0.82
            and persona_score >= 0.70
        ):
            return _ResolvedAvatarTier(
                quality_tier="signature_live",
                quality_percentage=100,
                presentation_mode="realtime_replica",
                voice_mode="personalized",
                generation_status="production_ready",
                user_title="Presence ready",
                user_message=(
                    "Your most complete and natural presence is ready."
                ),
                gesture_intensity="identity_conditioned",
                facial_expression_intensity="identity_conditioned",
                idle_motion="natural",
            )

        if (
            identity_score >= 0.80
            and motion_score >= 0.68
            and voice_score >= 0.65
        ):
            quality = self._bounded_percentage(
                overall_score,
                minimum=82,
                maximum=94,
            )

            return _ResolvedAvatarTier(
                quality_tier="expressive_live",
                quality_percentage=quality,
                presentation_mode="controlled_talking_avatar",
                voice_mode="personalized_when_ready",
                generation_status="production_ready",
                user_title="Presence ready",
                user_message=(
                    "A natural speaking presence is ready."
                ),
                gesture_intensity="restrained_identity_conditioned",
                facial_expression_intensity="natural",
                idle_motion="subtle",
            )

        if (
            identity_score >= 0.72
            and (
                motion_score >= 0.48
                or voice_score >= 0.55
            )
        ):
            quality = self._bounded_percentage(
                overall_score,
                minimum=68,
                maximum=81,
            )

            return _ResolvedAvatarTier(
                quality_tier="guided_live",
                quality_percentage=quality,
                presentation_mode="guided_talking_avatar",
                voice_mode=(
                    "personalized_when_ready"
                    if voice_score >= 0.55
                    else "warm_default"
                ),
                generation_status="production_ready_degraded",
                user_title="Presence ready",
                user_message=(
                    "A calm, carefully guided speaking presence is ready."
                ),
                gesture_intensity="minimal",
                facial_expression_intensity="controlled",
                idle_motion="minimal",
            )

        if identity_score >= 0.58:
            quality = self._bounded_percentage(
                overall_score,
                minimum=48,
                maximum=67,
            )

            return _ResolvedAvatarTier(
                quality_tier="cinematic_portrait",
                quality_percentage=quality,
                presentation_mode="cinematic_portrait",
                voice_mode=(
                    "personalized_when_ready"
                    if voice_score >= 0.55
                    else "warm_default"
                ),
                generation_status="production_ready_degraded",
                user_title="Presence ready",
                user_message=(
                    "A calm visual presence is ready for conversation."
                ),
                gesture_intensity="none",
                facial_expression_intensity="none",
                idle_motion="breathing_light_only",
            )

        quality = self._bounded_percentage(
            max(overall_score, persona_score),
            minimum=25,
            maximum=47,
        )

        return _ResolvedAvatarTier(
            quality_tier="premium_presence",
            quality_percentage=quality,
            presentation_mode="abstract_presence",
            voice_mode=(
                "personalized_when_ready"
                if voice_score >= 0.55
                else "warm_default"
            ),
            generation_status="production_ready_degraded",
            user_title="Presence ready",
            user_message=(
                "A private voice-first presence is ready."
            ),
            gesture_intensity="none",
            facial_expression_intensity="none",
            idle_motion="ambient",
        )

    def _overall_score(
        self,
        identity_score: float,
        motion_score: float,
        voice_score: float,
        persona_score: float,
        consent_score: float,
    ) -> float:
        if consent_score <= 0.0:
            return 0.0

        score = (
            identity_score * 0.34
            + motion_score * 0.24
            + voice_score * 0.22
            + persona_score * 0.14
            + consent_score * 0.06
        )

        return round(
            self._clamp(score),
            3,
        )

    def _capabilities(
        self,
        tier: _ResolvedAvatarTier,
        identity_score: float,
        motion_score: float,
        voice_score: float,
        persona_score: float,
        consent_score: float,
    ) -> List[AvatarCapabilityDecision]:
        return [
            self._decision(
                capability="visual_presence",
                score=identity_score,
                status=(
                    "enabled"
                    if consent_score > 0.0
                    else "disabled"
                ),
                reason=(
                    "Every permitted profile receives a stable visual "
                    "presence appropriate to its available evidence."
                ),
            ),
            self._decision(
                capability="identity_preserving_face",
                score=identity_score,
                status=(
                    "enabled"
                    if identity_score >= 0.72
                    else (
                        "degraded"
                        if identity_score >= 0.58
                        else "disabled"
                    )
                ),
                reason=(
                    "Facial rendering intensity is limited by identity "
                    "stability to prevent uncanny or misleading output."
                ),
            ),
            self._decision(
                capability="identity_conditioned_motion",
                score=motion_score,
                status=(
                    "enabled"
                    if tier.quality_tier in {
                        "signature_live",
                        "expressive_live",
                    }
                    else (
                        "degraded"
                        if tier.quality_tier == "guided_live"
                        else "disabled"
                    )
                ),
                reason=(
                    "Motion is enabled only when sufficient talking and "
                    "expression evidence is available."
                ),
            ),
            self._decision(
                capability="personalized_voice",
                score=voice_score,
                status=(
                    "enabled"
                    if voice_score >= 0.82
                    else (
                        "degraded"
                        if voice_score >= 0.55
                        else "disabled"
                    )
                ),
                reason=(
                    "A warm default voice remains available whenever the "
                    "personalized voice is not production-ready."
                ),
            ),
            self._decision(
                capability="persona_conditioned_response",
                score=persona_score,
                status=(
                    "enabled"
                    if persona_score >= 0.70
                    else (
                        "degraded"
                        if persona_score >= 0.45
                        else "disabled"
                    )
                ),
                reason=(
                    "Response style is conditioned only by sufficiently "
                    "grounded memories and persona evidence."
                ),
            ),
        ]

    def _decision(
        self,
        capability: str,
        score: float,
        status: str,
        reason: str,
    ) -> AvatarCapabilityDecision:
        return AvatarCapabilityDecision(
            capability=capability,
            status=status,
            score=round(
                self._clamp(score),
                3,
            ),
            reason=reason,
        )

    def _blockers(
        self,
        consent_score: float,
        identity_score: float,
        motion_score: float,
        voice_score: float,
    ) -> List[str]:
        if consent_score <= 0.0:
            return [
                "Consent is required before avatar generation."
            ]

        blockers: List[str] = []

        if identity_score < 0.58:
            blockers.append(
                "Visual identity evidence is not yet strong enough "
                "for a recognizable face."
            )

        if motion_score < 0.48:
            blockers.append(
                "Motion evidence is not yet strong enough for "
                "trustworthy facial animation."
            )

        if voice_score < 0.55:
            blockers.append(
                "Voice evidence is not yet strong enough for a "
                "personalized production voice."
            )

        return blockers

    def _next_best_actions(
        self,
        tier: _ResolvedAvatarTier,
        identity_score: float,
        motion_score: float,
        voice_score: float,
        persona_score: float,
    ) -> List[str]:
        if tier.quality_tier == "signature_live":
            return [
                "Your highest-quality presence is ready."
            ]

        actions: List[str] = []

        if motion_score < 0.82:
            actions.append(
                "Add a calm 30–60 second video with the full face "
                "and upper shoulders visible."
            )

        if voice_score < 0.82:
            actions.append(
                "Add 2–5 minutes of clear, natural speech."
            )

        if identity_score < 0.88:
            actions.append(
                "Add two clear photos from slightly different angles."
            )

        if persona_score < 0.70:
            actions.append(
                "Add more stories and personal memories."
            )

        return actions[:3]

    def _bounded_percentage(
        self,
        score: float,
        minimum: int,
        maximum: int,
    ) -> int:
        raw_percentage = int(
            round(
                self._clamp(score) * 100
            )
        )

        return min(
            maximum,
            max(
                minimum,
                raw_percentage,
            ),
        )

    def _clamp(
        self,
        value: float,
    ) -> float:
        return max(
            0.0,
            min(
                float(value),
                1.0,
            ),
        )
