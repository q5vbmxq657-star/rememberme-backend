from app.schemas.avatar_identity import (
    AvatarIdentityBlueprintRequest,
    AvatarIdentityBlueprintResponse,
)


class AvatarIdentityBlueprintService:
    def build(
        self,
        request: AvatarIdentityBlueprintRequest
    ) -> AvatarIdentityBlueprintResponse:
        missing = []

        if request.consent_safety_score < 1.0:
            missing.append("Verified avatar reconstruction consent is required.")

        if request.visual_identity_score < 0.62:
            missing.append("More clear frontal face images or videos are required.")

        if request.voice_identity_score < 0.62:
            missing.append("More clean voice material is required for stable voice identity.")

        if request.behavioral_persona_score < 0.62:
            missing.append("More memories, phrases and behavioral evidence are required.")

        blueprint_status = "ready" if not missing else "incomplete"

        return AvatarIdentityBlueprintResponse(
            profile_id=request.profile_id,
            blueprint_status=blueprint_status,
            realism_mode=self._realism_mode(request),
            voice_strategy=self._voice_strategy(request),
            visual_strategy=self._visual_strategy(request),
            behavior_strategy=self._behavior_strategy(request),
            lip_sync_strategy=self._lip_sync_strategy(request),
            safety_constraints=[
                "Never claim to be the real person.",
                "Always position output as AI-generated remembrance.",
                "Use uncertainty when memory evidence is weak.",
                "Disable export, download and sharing for avatar sessions.",
                "Reduce immersion when emotional dependency risk is elevated."
            ],
            missing_requirements=missing,
            next_pipeline_step=self._next_pipeline_step(blueprint_status, request)
        )

    def _realism_mode(self, request: AvatarIdentityBlueprintRequest) -> str:
        if request.readiness_level == "avatar_ready":
            return "controlled_photorealism"
        if request.readiness_level == "training_ready":
            return "guided_realism_preview"
        return "non_photoreal_placeholder"

    def _voice_strategy(self, request: AvatarIdentityBlueprintRequest) -> str:
        if request.voice_identity_score >= 0.82:
            return "voice_clone_candidate"
        if request.voice_identity_score >= 0.62:
            return "speaker_conditioned_tts"
        return "generic_warm_tts"

    def _visual_strategy(self, request: AvatarIdentityBlueprintRequest) -> str:
        if request.visual_identity_score >= 0.82:
            return "photoreal_talking_portrait_candidate"
        if request.visual_identity_score >= 0.62:
            return "controlled_face_reference_preview"
        return "abstract_presence_orb"

    def _behavior_strategy(self, request: AvatarIdentityBlueprintRequest) -> str:
        if request.behavioral_persona_score >= 0.82:
            return "persona_vector_conditioning"
        if request.behavioral_persona_score >= 0.62:
            return "soft_persona_prompt_conditioning"
        return "memory_grounded_neutral_response"

    def _lip_sync_strategy(self, request: AvatarIdentityBlueprintRequest) -> str:
        if request.visual_identity_score >= 0.62 and request.voice_identity_score >= 0.62:
            return "audio_driven_viseme_mapping"
        return "disabled_until_voice_and_visual_ready"

    def _next_pipeline_step(
        self,
        blueprint_status: str,
        request: AvatarIdentityBlueprintRequest
    ) -> str:
        if blueprint_status != "ready":
            return "collect_missing_identity_data"

        if request.voice_identity_score >= 0.82 and request.visual_identity_score >= 0.82:
            return "generate_controlled_talking_portrait_mvp"

        return "prepare_guided_realism_preview"
