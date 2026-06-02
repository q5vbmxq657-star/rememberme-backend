from app.schemas.avatar_training import (
    AvatarTrainingReadinessRequest,
    AvatarTrainingReadinessResponse,
    AvatarTrainingGap,
)


class AvatarTrainingReadinessService:
    def assess(
        self,
        request: AvatarTrainingReadinessRequest
    ) -> AvatarTrainingReadinessResponse:
        visual_score = self._visual_identity_score(request)
        voice_score = self._voice_identity_score(request)
        persona_score = self._behavioral_persona_score(request)
        consent_score = 1.0 if request.consent_verified else 0.0

        overall = round(
            (
                visual_score * 0.30 +
                voice_score * 0.30 +
                persona_score * 0.25 +
                consent_score * 0.15
            ),
            3
        )

        gaps = self._gaps(
            request=request,
            visual_score=visual_score,
            voice_score=voice_score,
            persona_score=persona_score,
            consent_score=consent_score,
        )

        return AvatarTrainingReadinessResponse(
            profile_id=request.profile_id,
            visual_identity_score=visual_score,
            voice_identity_score=voice_score,
            behavioral_persona_score=persona_score,
            consent_safety_score=consent_score,
            overall_readiness_score=overall,
            readiness_level=self._readiness_level(overall),
            gaps=gaps,
            next_best_actions=[gap.recommendation for gap in gaps[:3]]
        )

    def _visual_identity_score(self, request: AvatarTrainingReadinessRequest) -> float:
        visual_assets = [
            asset for asset in request.assets
            if asset.type in ["image", "video"]
        ]

        if not visual_assets:
            return 0.0

        face_assets = [asset for asset in visual_assets if asset.has_face]
        frontal_assets = [asset for asset in visual_assets if asset.has_frontal_face]
        lighting_assets = [asset for asset in visual_assets if asset.has_clear_lighting]

        score = 0.0
        score += min(len(visual_assets) / 12.0, 0.30)
        score += min(len(face_assets) / 8.0, 0.30)
        score += min(len(frontal_assets) / 5.0, 0.25)
        score += min(len(lighting_assets) / 5.0, 0.15)

        return round(min(score, 1.0), 3)

    def _voice_identity_score(self, request: AvatarTrainingReadinessRequest) -> float:
        voice_assets = [
            asset for asset in request.assets
            if asset.type in ["voice", "video"] and asset.has_voice
        ]

        total_duration = sum(
            asset.duration_seconds or 0.0
            for asset in voice_assets
        )

        quality_average = self._quality_average(voice_assets)

        score = 0.0
        score += min(total_duration / 300.0, 0.60)
        score += min(len(voice_assets) / 8.0, 0.20)
        score += quality_average * 0.20

        return round(min(score, 1.0), 3)

    def _behavioral_persona_score(self, request: AvatarTrainingReadinessRequest) -> float:
        memory_component = min(request.memory_count / 30.0, 0.55)
        persona_component = min(request.persona_confidence_score, 1.0) * 0.45

        return round(min(memory_component + persona_component, 1.0), 3)

    def _quality_average(self, assets) -> float:
        scores = [
            asset.quality_score
            for asset in assets
            if asset.quality_score is not None
        ]

        if not scores:
            return 0.35

        return min(max(sum(scores) / len(scores), 0.0), 1.0)

    def _gaps(
        self,
        request: AvatarTrainingReadinessRequest,
        visual_score: float,
        voice_score: float,
        persona_score: float,
        consent_score: float,
    ):
        gaps = []

        if consent_score < 1.0:
            gaps.append(
                AvatarTrainingGap(
                    area="Consent",
                    severity="critical",
                    recommendation="Verify consent before enabling photorealistic avatar generation."
                )
            )

        if visual_score < 0.55:
            gaps.append(
                AvatarTrainingGap(
                    area="Visual Identity",
                    severity="high",
                    recommendation="Add 5-10 clear face images or short videos with frontal lighting."
                )
            )

        if voice_score < 0.55:
            gaps.append(
                AvatarTrainingGap(
                    area="Voice Identity",
                    severity="high",
                    recommendation="Add at least 3-5 minutes of clear voice recordings."
                )
            )

        if persona_score < 0.55:
            gaps.append(
                AvatarTrainingGap(
                    area="Behavioral Persona",
                    severity="medium",
                    recommendation="Add more stories, phrases, routines and emotional memories."
                )
            )

        if not gaps:
            gaps.append(
                AvatarTrainingGap(
                    area="Avatar Foundation",
                    severity="low",
                    recommendation="Avatar foundation is ready for controlled realism MVP generation."
                )
            )

        return gaps

    def _readiness_level(self, score: float) -> str:
        if score >= 0.82:
            return "avatar_ready"
        if score >= 0.62:
            return "training_ready"
        if score >= 0.38:
            return "foundation_needed"
        return "insufficient_data"
