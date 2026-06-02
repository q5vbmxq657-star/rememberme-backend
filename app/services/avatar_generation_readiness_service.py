from typing import List

from app.schemas.avatar_generation_readiness import (
    AvatarGenerationReadinessRequest,
    AvatarGenerationReadinessResponse,
    AvatarCapabilityDecision,
)
from app.schemas.avatar_identity_fusion import AvatarIdentityFusionRequest
from app.schemas.avatar_motion import AvatarMotionReadinessRequest

from app.services.avatar_identity_fusion_service import AvatarIdentityFusionService
from app.services.avatar_motion_readiness_service import AvatarMotionReadinessService


class AvatarGenerationReadinessService:
    def __init__(self):
        self.identity_fusion_service = AvatarIdentityFusionService()
        self.motion_service = AvatarMotionReadinessService()

    def assess(
        self,
        request: AvatarGenerationReadinessRequest
    ) -> AvatarGenerationReadinessResponse:
        identity = self.identity_fusion_service.fuse(
            AvatarIdentityFusionRequest(
                profile_id=request.profile_id,
                min_quality_score=0.70
            )
        )

        motion = self.motion_service.assess(
            AvatarMotionReadinessRequest(
                profile_id=request.profile_id
            )
        )

        identity_score = identity.identity_stability_score
        motion_score = motion.talking_portrait_readiness_score
        voice_score = self._clamp(request.voice_identity_score)
        persona_score = self._clamp(request.behavioral_persona_score)
        consent_score = 1.0 if request.consent_verified else 0.0

        overall_score = self._overall_score(
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
            persona_score=persona_score,
            consent_score=consent_score
        )

        capabilities = self._capabilities(
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
            persona_score=persona_score,
            consent_score=consent_score
        )

        blockers = self._blockers(
            consent_score=consent_score,
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score
        )

        recommended_mode = self._recommended_mode(
            consent_score=consent_score,
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
            persona_score=persona_score
        )

        return AvatarGenerationReadinessResponse(
            profile_id=request.profile_id,
            generation_status=self._generation_status(
                recommended_mode=recommended_mode,
                blockers=blockers
            ),
            recommended_avatar_mode=recommended_mode,
            overall_generation_score=overall_score,
            identity_score=identity_score,
            motion_score=motion_score,
            voice_score=voice_score,
            persona_score=persona_score,
            consent_score=consent_score,
            primary_identity_asset_id=identity.primary_reference_asset_id,
            primary_motion_asset_id=motion.recommended_primary_motion_asset_id,
            capabilities=capabilities,
            blockers=blockers,
            next_best_actions=self._next_best_actions(
                recommended_mode=recommended_mode,
                blockers=blockers,
                identity_score=identity_score,
                motion_score=motion_score,
                voice_score=voice_score,
                persona_score=persona_score
            )
        )

    def _overall_score(
        self,
        identity_score: float,
        motion_score: float,
        voice_score: float,
        persona_score: float,
        consent_score: float
    ) -> float:
        if consent_score <= 0:
            return 0.0

        score = (
            identity_score * 0.32
            + motion_score * 0.24
            + voice_score * 0.22
            + persona_score * 0.14
            + consent_score * 0.08
        )

        return round(self._clamp(score), 3)

    def _capabilities(
        self,
        identity_score: float,
        motion_score: float,
        voice_score: float,
        persona_score: float,
        consent_score: float
    ) -> List[AvatarCapabilityDecision]:
        return [
            self._decision(
                capability="abstract_presence",
                score=max(identity_score, persona_score),
                allowed=consent_score > 0,
                reason="Safe abstract avatar presence can run with minimal identity evidence."
            ),
            self._decision(
                capability="guided_face_preview",
                score=identity_score,
                allowed=consent_score > 0 and identity_score >= 0.70,
                reason="Requires stable visual identity references."
            ),
            self._decision(
                capability="controlled_talking_portrait",
                score=min(identity_score, motion_score),
                allowed=consent_score > 0 and identity_score >= 0.74 and motion_score >= 0.64,
                reason="Requires stable identity and at least one usable motion reference."
            ),
            self._decision(
                capability="speaker_conditioned_voice",
                score=voice_score,
                allowed=consent_score > 0 and voice_score >= 0.70,
                reason="Requires usable voice identity evidence."
            ),
            self._decision(
                capability="live_lip_sync",
                score=motion_score,
                allowed=consent_score > 0 and motion_score >= 0.64,
                reason="Requires sufficient lip visibility and talking portrait motion readiness."
            ),
            self._decision(
                capability="persona_conditioned_response",
                score=persona_score,
                allowed=consent_score > 0 and persona_score >= 0.55,
                reason="Requires extracted persona signals or memory-grounded behavioral context."
            )
        ]

    def _decision(
        self,
        capability: str,
        score: float,
        allowed: bool,
        reason: str
    ) -> AvatarCapabilityDecision:
        return AvatarCapabilityDecision(
            capability=capability,
            status="enabled" if allowed else "disabled",
            score=round(self._clamp(score), 3),
            reason=reason
        )

    def _blockers(
        self,
        consent_score: float,
        identity_score: float,
        motion_score: float,
        voice_score: float
    ) -> List[str]:
        blockers = []

        if consent_score <= 0:
            blockers.append("Consent is required before avatar generation.")

        if identity_score < 0.70:
            blockers.append("Stable visual identity references are required.")

        if motion_score < 0.64:
            blockers.append("A usable motion reference is required for talking portrait and lip sync.")

        if voice_score < 0.70:
            blockers.append("Voice identity is not ready for speaker-conditioned voice.")

        return blockers

    def _recommended_mode(
        self,
        consent_score: float,
        identity_score: float,
        motion_score: float,
        voice_score: float,
        persona_score: float
    ) -> str:
        if consent_score <= 0:
            return "blocked_until_consent"

        if identity_score >= 0.74 and motion_score >= 0.64 and voice_score >= 0.70:
            return "controlled_talking_portrait_with_voice"

        if identity_score >= 0.74 and motion_score >= 0.64:
            return "controlled_talking_portrait_generic_voice"

        if identity_score >= 0.70:
            return "guided_face_preview"

        if persona_score >= 0.55:
            return "abstract_persona_presence"

        return "safe_memory_chat_only"

    def _generation_status(
        self,
        recommended_mode: str,
        blockers: List[str]
    ) -> str:
        if recommended_mode == "blocked_until_consent":
            return "blocked"

        if recommended_mode in [
            "controlled_talking_portrait_with_voice",
            "controlled_talking_portrait_generic_voice"
        ]:
            return "avatar_generation_ready"

        if recommended_mode in [
            "guided_face_preview",
            "abstract_persona_presence"
        ]:
            return "partial_avatar_ready"

        return "not_ready"

    def _next_best_actions(
        self,
        recommended_mode: str,
        blockers: List[str],
        identity_score: float,
        motion_score: float,
        voice_score: float,
        persona_score: float
    ) -> List[str]:
        actions = []

        if recommended_mode == "controlled_talking_portrait_with_voice":
            actions.append("Avatar is ready for controlled talking portrait generation with speaker-conditioned voice.")
            return actions

        if motion_score < 0.82:
            actions.append("Upload a longer 20–45 second talking video to improve expression and lip-sync quality.")

        if voice_score < 0.70:
            actions.append("Upload at least 60–120 seconds of clear speech for voice identity readiness.")

        if identity_score < 0.86:
            actions.append("Add 2–3 high-quality face references from different angles.")

        if persona_score < 0.70:
            actions.append("Add more stories and memories to strengthen persona conditioning.")

        if blockers:
            actions.extend(blockers)

        return list(dict.fromkeys(actions))

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))
