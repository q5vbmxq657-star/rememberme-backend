from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional
from uuid import UUID

from app.models.avatar_evidence_asset import (
    AvatarEvidenceAsset,
)
from app.models.digital_human_profile import (
    DigitalHumanProfile,
)
from app.schemas.avatar_generation_readiness import (
    AvatarCapabilityDecision,
    AvatarGenerationReadinessRequest,
    AvatarGenerationReadinessResponse,
    AvatarPresentationSpec,
)
from app.schemas.avatar_identity_fusion import (
    AvatarIdentityFusionRequest,
)
from app.schemas.avatar_motion import (
    AvatarMotionReadinessRequest,
)
from app.services.avatar_evidence_repository import (
    AvatarEvidenceRepository,
)
from app.services.avatar_identity_fusion_service import (
    AvatarIdentityFusionService,
)
from app.services.avatar_motion_readiness_service import (
    AvatarMotionReadinessService,
)
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileRepository,
)


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
    Canonical server-side quality resolver.

    Security and trust boundary:

    - The client supplies only profile_id.
    - Consent is loaded from PostgreSQL.
    - Personalized voice readiness is loaded from PostgreSQL.
    - Persisted avatar readiness is loaded from PostgreSQL.
    - Visual and motion readiness is derived exclusively from persistent avatar_evidence_assets.
    - Persona evidence is read from persistent profile metadata.

    The client cannot upgrade consent, voice readiness, visual readiness,
    motion readiness, avatar status or quality by sending crafted scores.
    """

    def __init__(
        self,
        *,
        repository: Optional[
            DigitalHumanProfileRepository
        ] = None,
        evidence_repository: Optional[
            AvatarEvidenceRepository
        ] = None,
        identity_fusion_service: Optional[
            AvatarIdentityFusionService
        ] = None,
        motion_service: Optional[
            AvatarMotionReadinessService
        ] = None,
    ) -> None:
        """
        Create the canonical readiness resolver.

        identity_fusion_service and motion_service remain accepted only for
        backwards-compatible dependency construction. They are intentionally
        not used by assess(). Visual readiness is derived exclusively from
        persistent AvatarEvidenceRepository records.
        """

        self._repository = repository
        self._evidence_repository = (
            evidence_repository
        )

        self.identity_fusion_service = (
            identity_fusion_service
        )

        self.motion_service = (
            motion_service
        )

    @property
    def repository(
        self,
    ) -> DigitalHumanProfileRepository:
        """
        Resolve persistence only when an operation actually needs it.

        Pure quality-tier calculations remain testable without DATABASE_URL.
        Production assessment still requires the canonical PostgreSQL
        repository unless a repository is explicitly injected.
        """

        if self._repository is None:
            self._repository = (
                DigitalHumanProfileRepository()
            )

        return self._repository

    @property
    def evidence_repository(
        self,
    ) -> AvatarEvidenceRepository:
        """
        Resolve the persistent visual evidence source lazily.

        This repository is the only authority for identity and motion
        readiness. Legacy avatar-media services cannot raise visual scores.
        """

        if self._evidence_repository is None:
            self._evidence_repository = (
                AvatarEvidenceRepository()
            )

        return self._evidence_repository

    def assess(
        self,
        request: AvatarGenerationReadinessRequest,
    ) -> AvatarGenerationReadinessResponse:
        profile = self.repository.require(
            request.profile_id
        )

        identity_assets = (
            self.evidence_repository
            .list_active_assets(
                request.profile_id,
                evidence_kind="identity_photo",
            )
        )

        motion_assets = (
            self.evidence_repository
            .list_active_assets(
                request.profile_id,
                evidence_kind="motion_video",
            )
        )

        primary_identity_asset = (
            self.evidence_repository
            .resolve_primary(
                request.profile_id,
                "identity_photo",
            )
        )

        primary_motion_asset = (
            self.evidence_repository
            .resolve_primary(
                request.profile_id,
                "motion_video",
            )
        )

        identity_score = (
            self._identity_score_from_evidence(
                identity_assets
            )
        )

        motion_score = (
            self._motion_score_from_evidence(
                motion_assets
            )
        )

        voice_score = self._resolve_voice_score(
            profile
        )

        persona_score = self._resolve_persona_score(
            profile
        )

        consent_score = (
            1.0
            if profile.consent_verified
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
            quality_tier=(
                resolved_tier.quality_tier
            ),
            quality_percentage=(
                resolved_tier.quality_percentage
            ),
            presentation_mode=(
                resolved_tier.presentation_mode
            ),
            voice_mode=resolved_tier.voice_mode,
            framing="medium_close_up",
            camera_motion="locked",
            gesture_intensity=(
                resolved_tier.gesture_intensity
            ),
            facial_expression_intensity=(
                resolved_tier
                .facial_expression_intensity
            ),
            idle_motion=resolved_tier.idle_motion,
            show_full_face=True,
            show_upper_shoulders=True,
            allow_aggressive_close_up=False,
            user_title=resolved_tier.user_title,
            user_message=resolved_tier.user_message,
        )

        self.repository.update_quality(
            request.profile_id,
            quality_tier=(
                resolved_tier.quality_tier
            ),
            quality_percentage=(
                resolved_tier.quality_percentage
            ),
            metadata={
                "server_authoritative_readiness": {
                    "overall_score": overall_score,
                    "identity_score": identity_score,
                    "motion_score": motion_score,
                    "voice_score": voice_score,
                    "persona_score": persona_score,
                    "consent_score": consent_score,
                    "generation_status": (
                        resolved_tier
                        .generation_status
                    ),
                    "presentation_mode": (
                        resolved_tier
                        .presentation_mode
                    ),
                }
            },
        )

        return AvatarGenerationReadinessResponse(
            profile_id=request.profile_id,
            generation_status=(
                resolved_tier.generation_status
            ),
            recommended_avatar_mode=(
                resolved_tier.presentation_mode
            ),
            quality_tier=(
                resolved_tier.quality_tier
            ),
            quality_percentage=(
                resolved_tier.quality_percentage
            ),
            overall_generation_score=(
                overall_score
            ),
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
            persona_score=persona_score,
            consent_score=consent_score,
            primary_identity_asset_id=(
                str(
                    primary_identity_asset.asset_id
                )
                if primary_identity_asset
                is not None
                else None
            ),
            primary_motion_asset_id=(
                str(
                    primary_motion_asset.asset_id
                )
                if primary_motion_asset
                is not None
                else None
            ),
            presentation=presentation,
            capabilities=capabilities,
            blockers=blockers,
            next_best_actions=(
                self._next_best_actions(
                    tier=resolved_tier,
                    identity_score=identity_score,
                    motion_score=motion_score,
                    voice_score=voice_score,
                    persona_score=persona_score,
                )
            ),
        )

    def _identity_score_from_evidence(
        self,
        assets: List[AvatarEvidenceAsset],
    ) -> float:
        """
        Resolve identity readiness solely from eligible persistent evidence.

        list_active_assets() already excludes:
        - archived assets
        - removed or non-included assets
        - rejected assets
        - assets below quality 0.72
        - assets outside ready/training states
        """

        scores = [
            self._identity_asset_score(asset)
            for asset in assets
            if (
                asset.evidence_kind
                == "identity_photo"
                and asset.is_active_avatar_evidence
            )
        ]

        if not scores:
            return 0.0

        return round(
            max(scores),
            3,
        )

    def _identity_asset_score(
        self,
        asset: AvatarEvidenceAsset,
    ) -> float:
        score = (
            self._clamp(
                asset.quality_score
            ) * 0.30
            + self._clamp(
                asset.identity_consistency_score
            ) * 0.30
            + self._clamp(
                asset.emotional_presence_score
            ) * 0.10
            + (
                0.10
                if asset.has_face
                else 0.0
            )
            + (
                0.10
                if asset.has_frontal_face
                else 0.0
            )
            + (
                0.10
                if asset.has_clear_lighting
                else 0.0
            )
        )

        return self._clamp(score)

    def _motion_score_from_evidence(
        self,
        assets: List[AvatarEvidenceAsset],
    ) -> float:
        """
        Resolve motion readiness solely from eligible persistent evidence.
        """

        scores = [
            self._motion_asset_score(asset)
            for asset in assets
            if (
                asset.evidence_kind
                == "motion_video"
                and asset.is_active_avatar_evidence
            )
        ]

        if not scores:
            return 0.0

        return round(
            max(scores),
            3,
        )

    def _motion_asset_score(
        self,
        asset: AvatarEvidenceAsset,
    ) -> float:
        score = (
            self._clamp(
                asset.motion_quality_score
            ) * 0.25
            + self._clamp(
                asset.expression_range_score
            ) * 0.20
            + self._clamp(
                asset.lip_visibility_score
            ) * 0.20
            + self._clamp(
                asset.head_pose_stability_score
            ) * 0.15
            + self._clamp(
                asset.quality_score
            ) * 0.10
            + (
                0.10
                if asset.motion_usable
                else 0.0
            )
        )

        return self._clamp(score)

    def _resolve_voice_score(
        self,
        profile: DigitalHumanProfile,
    ) -> float:
        if profile.has_personalized_voice:
            return 1.0

        status = (
            profile.voice_training_status
            or ""
        ).strip().lower()

        if (
            status
            in {
                "submitted",
                "training",
                "processing",
            }
            and profile.voice_provider
        ):
            return 0.55

        return 0.0

    def _resolve_persona_score(
        self,
        profile: DigitalHumanProfile,
    ) -> float:
        metadata = profile.metadata

        candidates = [
            self._nested_value(
                metadata,
                "persona",
                "confidence_score",
            ),
            self._nested_value(
                metadata,
                "persona",
                "readiness_score",
            ),
            self._nested_value(
                metadata,
                "training",
                "persona_score",
            ),
            metadata.get(
                "persona_confidence_score"
            ),
            metadata.get(
                "behavioral_persona_score"
            ),
        ]

        for candidate in candidates:
            parsed = self._optional_float(
                candidate
            )

            if parsed is not None:
                return self._clamp(parsed)

        return 0.0

    def _nested_value(
        self,
        payload: Mapping[str, Any],
        first_key: str,
        second_key: str,
    ) -> Any:
        first_value = payload.get(first_key)

        if not isinstance(
            first_value,
            Mapping,
        ):
            return None

        return first_value.get(second_key)

    def _optional_float(
        self,
        value: Any,
    ) -> Optional[float]:
        if value is None:
            return None

        if isinstance(
            value,
            bool,
        ):
            return None

        try:
            return float(value)
        except (
            TypeError,
            ValueError,
        ):
            return None

    def _resolve_quality_tier(
        self,
        *,
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
                    "Permission is required before "
                    "creating this presence."
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
                presentation_mode=(
                    "realtime_replica"
                ),
                voice_mode="personalized",
                generation_status=(
                    "production_ready"
                ),
                user_title="Presence ready",
                user_message=(
                    "Your most complete and natural "
                    "presence is ready."
                ),
                gesture_intensity=(
                    "identity_conditioned"
                ),
                facial_expression_intensity=(
                    "identity_conditioned"
                ),
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
                presentation_mode=(
                    "controlled_talking_avatar"
                ),
                voice_mode=(
                    "personalized_when_ready"
                ),
                generation_status=(
                    "production_ready"
                ),
                user_title="Presence ready",
                user_message=(
                    "A natural speaking presence "
                    "is ready."
                ),
                gesture_intensity=(
                    "restrained_identity_conditioned"
                ),
                facial_expression_intensity=(
                    "natural"
                ),
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
                presentation_mode=(
                    "guided_talking_avatar"
                ),
                voice_mode=(
                    "personalized_when_ready"
                    if voice_score >= 0.55
                    else "warm_default"
                ),
                generation_status=(
                    "production_ready_degraded"
                ),
                user_title="Presence ready",
                user_message=(
                    "A calm, carefully guided "
                    "speaking presence is ready."
                ),
                gesture_intensity="minimal",
                facial_expression_intensity=(
                    "controlled"
                ),
                idle_motion="minimal",
            )

        if identity_score >= 0.58:
            quality = self._bounded_percentage(
                overall_score,
                minimum=48,
                maximum=67,
            )

            return _ResolvedAvatarTier(
                quality_tier=(
                    "cinematic_portrait"
                ),
                quality_percentage=quality,
                presentation_mode=(
                    "cinematic_portrait"
                ),
                voice_mode=(
                    "personalized_when_ready"
                    if voice_score >= 0.55
                    else "warm_default"
                ),
                generation_status=(
                    "production_ready_degraded"
                ),
                user_title="Presence ready",
                user_message=(
                    "A calm visual presence is "
                    "ready for conversation."
                ),
                gesture_intensity="none",
                facial_expression_intensity=(
                    "none"
                ),
                idle_motion=(
                    "breathing_light_only"
                ),
            )

        quality = self._bounded_percentage(
            max(
                overall_score,
                persona_score,
            ),
            minimum=25,
            maximum=47,
        )

        return _ResolvedAvatarTier(
            quality_tier="premium_presence",
            quality_percentage=quality,
            presentation_mode=(
                "abstract_presence"
            ),
            voice_mode=(
                "personalized_when_ready"
                if voice_score >= 0.55
                else "warm_default"
            ),
            generation_status=(
                "production_ready_degraded"
            ),
            user_title="Presence ready",
            user_message=(
                "A private voice-first presence "
                "is ready."
            ),
            gesture_intensity="none",
            facial_expression_intensity="none",
            idle_motion="ambient",
        )

    def _overall_score(
        self,
        *,
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
        *,
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
                    "Every permitted profile receives "
                    "a stable visual presence appropriate "
                    "to its available evidence."
                ),
            ),
            self._decision(
                capability=(
                    "identity_preserving_face"
                ),
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
                    "Facial rendering intensity is "
                    "limited by server-verified identity "
                    "stability."
                ),
            ),
            self._decision(
                capability=(
                    "identity_conditioned_motion"
                ),
                score=motion_score,
                status=(
                    "enabled"
                    if tier.quality_tier
                    in {
                        "signature_live",
                        "expressive_live",
                    }
                    else (
                        "degraded"
                        if tier.quality_tier
                        == "guided_live"
                        else "disabled"
                    )
                ),
                reason=(
                    "Motion is enabled only when "
                    "sufficient backend-verified motion "
                    "evidence is available."
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
                    "Personalized voice availability "
                    "is resolved from the persistent "
                    "server-side voice identity."
                ),
            ),
            self._decision(
                capability=(
                    "persona_conditioned_response"
                ),
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
                    "Persona conditioning is enabled "
                    "only from persistent grounded "
                    "persona evidence."
                ),
            ),
        ]

    def _decision(
        self,
        *,
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
        *,
        consent_score: float,
        identity_score: float,
        motion_score: float,
        voice_score: float,
    ) -> List[str]:
        if consent_score <= 0.0:
            return [
                (
                    "Consent is required before "
                    "avatar generation."
                )
            ]

        blockers: List[str] = []

        if identity_score < 0.58:
            blockers.append(
                (
                    "Visual identity evidence is not "
                    "yet strong enough for a "
                    "recognizable face."
                )
            )

        if motion_score < 0.48:
            blockers.append(
                (
                    "Motion evidence is not yet strong "
                    "enough for trustworthy facial "
                    "animation."
                )
            )

        if voice_score < 0.55:
            blockers.append(
                (
                    "A server-verified personalized "
                    "voice is not ready."
                )
            )

        return blockers

    def _next_best_actions(
        self,
        *,
        tier: _ResolvedAvatarTier,
        identity_score: float,
        motion_score: float,
        voice_score: float,
        persona_score: float,
    ) -> List[str]:
        if tier.quality_tier == "signature_live":
            return [
                (
                    "Your highest-quality presence "
                    "is ready."
                )
            ]

        actions: List[str] = []

        if motion_score < 0.82:
            actions.append(
                (
                    "Add a calm 30–60 second video "
                    "with the full face and upper "
                    "shoulders visible."
                )
            )

        if voice_score < 0.82:
            actions.append(
                (
                    "Complete server-verified "
                    "personalized voice training."
                )
            )

        if identity_score < 0.88:
            actions.append(
                (
                    "Add two clear photos from "
                    "slightly different angles."
                )
            )

        if persona_score < 0.70:
            actions.append(
                (
                    "Add more grounded stories and "
                    "personal memories."
                )
            )

        return actions[:3]

    def _bounded_percentage(
        self,
        score: float,
        *,
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
        value: Any,
    ) -> float:
        try:
            parsed = float(value)
        except (
            TypeError,
            ValueError,
        ):
            parsed = 0.0

        return max(
            0.0,
            min(
                parsed,
                1.0,
            ),
        )
