from app.schemas.avatar_runtime import (
    AvatarRuntimePlanRequest,
    AvatarRuntimePlanResponse,
)


class AvatarRuntimePlanService:
    def build(self, request: AvatarRuntimePlanRequest) -> AvatarRuntimePlanResponse:
        disabled = []

        if request.blueprint_status != "ready":
            return AvatarRuntimePlanResponse(
                profile_id=request.profile_id,
                runtime_status="blocked",
                session_mode="safe_memory_chat",
                visual_renderer="abstract_orb",
                voice_renderer="generic_tts",
                lip_sync_runtime="disabled",
                behavior_conditioning="memory_grounded_neutral_response",
                latency_target_ms=1800,
                fallback_mode="text_and_generic_voice",
                disabled_capabilities=[
                    "photorealistic_avatar",
                    "voice_clone",
                    "lip_sync",
                    "gesture_runtime"
                ],
                required_client_features=[
                    "memory_chat",
                    "guardian_indicator",
                    "confidence_labels"
                ]
            )

        if request.realism_mode == "controlled_photorealism":
            visual_renderer = self._visual_renderer(request.visual_strategy)
            voice_renderer = self._voice_renderer(request.voice_strategy)
            lip_sync_runtime = self._lip_sync_runtime(request.lip_sync_strategy)
            behavior = request.behavior_strategy
            session_mode = "guided_realism_avatar_session"
            latency = 900
        else:
            visual_renderer = "abstract_orb"
            voice_renderer = "generic_tts"
            lip_sync_runtime = "disabled"
            behavior = "memory_grounded_neutral_response"
            session_mode = "safe_memory_chat"
            latency = 1500

        if voice_renderer == "generic_tts":
            disabled.append("voice_identity_clone")

        if lip_sync_runtime == "disabled":
            disabled.append("live_lip_sync")

        if visual_renderer in ["abstract_orb", "static_reference_card"]:
            disabled.append("photorealistic_motion")

        return AvatarRuntimePlanResponse(
            profile_id=request.profile_id,
            runtime_status="ready",
            session_mode=session_mode,
            visual_renderer=visual_renderer,
            voice_renderer=voice_renderer,
            lip_sync_runtime=lip_sync_runtime,
            behavior_conditioning=behavior,
            latency_target_ms=latency,
            fallback_mode="text_streaming_with_safe_voice",
            disabled_capabilities=disabled,
            required_client_features=[
                "streaming_memory_chat",
                "voice_capture",
                "tts_playback",
                "guardian_indicator",
                "emotional_mode_display",
                "avatar_presence_surface"
            ]
        )

    def _visual_renderer(self, strategy: str) -> str:
        if strategy == "photoreal_talking_portrait_candidate":
            return "talking_portrait_renderer"
        if strategy == "controlled_face_reference_preview":
            return "guided_face_reference_renderer"
        return "abstract_orb"

    def _voice_renderer(self, strategy: str) -> str:
        if strategy == "voice_clone_candidate":
            return "speaker_conditioned_voice"
        if strategy == "speaker_conditioned_tts":
            return "speaker_conditioned_tts"
        return "generic_tts"

    def _lip_sync_runtime(self, strategy: str) -> str:
        if strategy == "audio_driven_viseme_mapping":
            return "viseme_mapping_runtime"
        return "disabled"
