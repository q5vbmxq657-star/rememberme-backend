from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.services.avatar_media_storage_service import AvatarMediaStorageService
from app.services.avatar_provider_service import AvatarProviderService
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileRepository,
)
from app.services.elevenlabs_voice_service import ElevenLabsVoiceService


class ProfileErasureServiceError(RuntimeError):
    pass


class ProfileErasureService:
    """Executes the canonical durable profile-erasure state machine."""

    def __init__(
        self,
        *,
        repository: DigitalHumanProfileRepository | None = None,
        media_storage: AvatarMediaStorageService | None = None,
        avatar_provider: AvatarProviderService | None = None,
        voice_service: ElevenLabsVoiceService | None = None,
    ) -> None:
        self.repository = repository or DigitalHumanProfileRepository()
        self.media_storage = media_storage or AvatarMediaStorageService()
        self.avatar_provider = avatar_provider or AvatarProviderService()
        self.voice_service = voice_service or ElevenLabsVoiceService(
            repository=self.repository
        )

    async def erase_profile(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
    ) -> None:
        request = self.repository.create_profile_erasure_request(
            request_id=uuid4(),
            profile_id=profile_id,
            idempotency_key=idempotency_key,
        )
        await self._run(request)

    async def _run(self, request: dict) -> None:
        request_id = UUID(str(request["request_id"]))
        profile_id = request.get("profile_id")
        if profile_id is None:
            return
        profile_id = UUID(str(profile_id))
        status = str(request["status"])

        if status == "retryable_failed":
            resume_stage = str(request.get("resume_stage") or "")
            request = self.repository.transition_profile_erasure_request(
                request_id=request_id,
                expected_status=status,
                new_status=resume_stage,
            )
            status = resume_stage

        if status == "provider_cleanup_required":
            request = self.repository.transition_profile_erasure_request(
                request_id=request_id,
                expected_status=status,
                new_status="provider_cleanup",
            )
            status = "provider_cleanup"

        try:
            if status == "requested":
                request = self.repository.transition_profile_erasure_request(
                    request_id=request_id,
                    expected_status=status,
                    new_status="provider_cleanup",
                )
                status = "provider_cleanup"

            if status == "provider_cleanup":
                snapshot = request.get("provider_snapshot") or {}
                await self.avatar_provider.delete_tavus_identity(
                    replica_id=snapshot.get("avatar_replica_id"),
                    persona_id=snapshot.get("avatar_persona_id"),
                )
                await self.voice_service.delete_profile_voice(
                    profile_id=profile_id
                )
                request = self.repository.transition_profile_erasure_request(
                    request_id=request_id,
                    expected_status=status,
                    new_status="storage_cleanup",
                )
                status = "storage_cleanup"

            if status == "storage_cleanup":
                asset_ids = self.media_storage.delete_profile_assets(
                    profile_id=str(profile_id)
                )
                request = self.repository.transition_profile_erasure_request(
                    request_id=request_id,
                    expected_status=status,
                    new_status="database_cleanup",
                    storage_asset_ids=asset_ids,
                )
                status = "database_cleanup"

            if status == "database_cleanup":
                self.repository.delete_profile_graph(profile_id=profile_id)
                self.repository.transition_profile_erasure_request(
                    request_id=request_id,
                    expected_status=status,
                    new_status="completed",
                )

        except Exception as error:
            retry_stage = status if status in {
                "provider_cleanup",
                "storage_cleanup",
                "database_cleanup",
            } else "requested"
            self.repository.transition_profile_erasure_request(
                request_id=request_id,
                expected_status=status,
                new_status="retryable_failed",
                resume_stage=retry_stage,
                next_retry_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                error_code="profile_erasure_failed",
                error_message="Profile erasure requires retry.",
            )
            raise ProfileErasureServiceError(
                "Profile erasure could not be verified."
            ) from error
