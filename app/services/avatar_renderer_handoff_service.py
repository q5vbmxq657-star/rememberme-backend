from app.schemas.avatar_renderer_handoff import (
    AvatarRendererHandoffRequest,
    AvatarRendererHandoffResponse,
    AvatarRendererInputAsset,
    AvatarRendererOutputContract,
)
from app.services.avatar_generation_job_service import AvatarGenerationJobService
from app.services.avatar_media_storage_service import AvatarMediaStorageService


class AvatarRendererHandoffService:
    def __init__(self):
        self.job_service = AvatarGenerationJobService()
        self.media_service = AvatarMediaStorageService()

    def build_handoff(
        self,
        request: AvatarRendererHandoffRequest,
        base_url: str
    ) -> AvatarRendererHandoffResponse:
        job = self.job_service.get_job(request.job_id)

        input_assets = self._build_input_assets(
            identity_asset_id=job.identity_asset_id,
            motion_asset_id=job.motion_asset_id,
            base_url=base_url,
            expires_in_seconds=request.expires_in_seconds
        )

        return AvatarRendererHandoffResponse(
            job_id=job.job_id,
            profile_id=job.profile_id,
            renderer_provider=request.renderer_provider,
            avatar_mode=job.generation_mode,
            renderer=self._renderer_for_mode(job.generation_mode),
            voice_strategy=self._voice_strategy(job.generation_mode),
            lip_sync_strategy=self._lip_sync_strategy(job.generation_mode),
            input_assets=input_assets,
            safety_constraints=self._safety_constraints(),
            output_contract=AvatarRendererOutputContract(
                expected_type="avatar_video_preview",
                format="mp4",
                storage_policy="store_as_signed_private_media_asset",
                requires_signed_delivery=True
            ),
            handoff_status="ready",
            message="Renderer handoff package is ready."
        )

    def _build_input_assets(
        self,
        identity_asset_id,
        motion_asset_id,
        base_url: str,
        expires_in_seconds: int
    ):
        if identity_asset_id and motion_asset_id and identity_asset_id == motion_asset_id:
            return [
                self._signed_asset(
                    asset_id=identity_asset_id,
                    role="identity_and_motion_reference",
                    base_url=base_url,
                    expires_in_seconds=expires_in_seconds
                )
            ]

        assets = []

        if identity_asset_id:
            assets.append(
                self._signed_asset(
                    asset_id=identity_asset_id,
                    role="identity_reference",
                    base_url=base_url,
                    expires_in_seconds=expires_in_seconds
                )
            )

        if motion_asset_id:
            assets.append(
                self._signed_asset(
                    asset_id=motion_asset_id,
                    role="motion_reference",
                    base_url=base_url,
                    expires_in_seconds=expires_in_seconds
                )
            )

        return assets

    def _signed_asset(
        self,
        asset_id: str,
        role: str,
        base_url: str,
        expires_in_seconds: int
    ) -> AvatarRendererInputAsset:
        metadata = self.media_service.get_metadata(asset_id)
        signed = self.media_service.sign_download_url(
            asset_id=asset_id,
            base_url=base_url,
            expires_in_seconds=expires_in_seconds
        )

        return AvatarRendererInputAsset(
            asset_id=asset_id,
            role=role,
            signed_url=signed.signed_url,
            content_type=metadata.content_type,
            expires_in_seconds=signed.expires_in_seconds
        )

    def _renderer_for_mode(
        self,
        generation_mode: str
    ) -> str:
        if generation_mode == "controlled_talking_portrait":
            return "talking_portrait_renderer"

        if generation_mode == "guided_face_preview":
            return "guided_face_reference_renderer"

        if generation_mode == "abstract_presence":
            return "abstract_presence_renderer"

        return "safe_memory_chat_renderer"

    def _voice_strategy(
        self,
        generation_mode: str
    ) -> str:
        if generation_mode == "controlled_talking_portrait":
            return "speaker_conditioned_or_safe_tts"

        return "safe_tts"

    def _lip_sync_strategy(
        self,
        generation_mode: str
    ) -> str:
        if generation_mode == "controlled_talking_portrait":
            return "audio_driven_viseme_mapping"

        return "disabled"

    def _safety_constraints(self):
        return [
            "Never claim the avatar is the real person.",
            "Always disclose AI-generated remembrance.",
            "Use only consent-approved private media.",
            "Do not export generated avatar output without explicit governance.",
            "Disable immersive rendering during crisis or high dependency mode.",
            "Fallback to text memory chat if renderer confidence is low."
        ]
