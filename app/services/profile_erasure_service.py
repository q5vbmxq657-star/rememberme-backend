from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.services.avatar_media_storage_service import AvatarMediaStorageService
from app.services.avatar_provider_service import AvatarProviderService
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileRepository,
)
from app.services.elevenlabs_voice_service import ElevenLabsVoiceService
from app.services.runtime_cleanup_repository import RuntimeCleanupRepository
from app.services.openai_realtime_registry import OpenAIRealtimeRegistry


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
        runtime_cleanup_repository: RuntimeCleanupRepository | None = None,
        openai_realtime_registry: OpenAIRealtimeRegistry | None = None,
    ) -> None:
        self.repository = repository or DigitalHumanProfileRepository()
        self.runtime_cleanup_repository = runtime_cleanup_repository or RuntimeCleanupRepository(
            database_url=self.repository.database_url
        )
        self.openai_realtime_registry = openai_realtime_registry or OpenAIRealtimeRegistry(
            database_url=self.repository.database_url
        )
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
        # Session locks survive awaits but are released on process/connection loss.
        # They serialize retries and foreground requests without holding a DB transaction.
        import psycopg

        database_url = getattr(self.repository, "database_url", None)
        if not isinstance(database_url, str):
            await self._run_stages(request)
            return
        with psycopg.connect(database_url, connect_timeout=10, autocommit=True) as connection:
            locked = connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 22))",
                (str(request["request_id"]),),
            ).fetchone()[0]
            if not locked:
                raise ProfileErasureServiceError("Profile erasure is already running.")
            current = self.repository.get_profile_erasure_request(
                request_id=UUID(str(request["request_id"]))
            )
            if current is None:
                raise ProfileErasureServiceError("Profile erasure request is unavailable.")
            await self._run_stages(current)

    async def resume_pending(self, *, limit: int = 100) -> dict[str, int]:
        from app.services.deletion_retention_service import DeletionRetentionService

        requests = DeletionRetentionService(
            database_url=self.repository.database_url
        ).pending_requests(limit=limit)
        result = {"completed": 0, "pending": 0}
        for request in requests:
            try:
                await self._run(request)
            except ProfileErasureServiceError:
                result["pending"] += 1
            else:
                result["completed"] += 1
        return result

    async def _run_stages(self, request: dict) -> None:
        request_id = UUID(str(request["request_id"]))
        profile_id = request.get("profile_id")
        status = str(request["status"])
        if profile_id is None:
            if status == "completed":
                return
            if status == "retryable_failed" and request.get("resume_stage") == "database_cleanup":
                self.repository.transition_profile_erasure_request(
                    request_id=request_id,
                    expected_status=status,
                    new_status="database_cleanup",
                )
                status = "database_cleanup"
            if status == "database_cleanup":
                # The profile FK is cleared by committed graph deletion. Provider and
                # storage cleanup already completed before reaching this stage.
                self.repository.transition_profile_erasure_request(
                    request_id=request_id,
                    expected_status=status,
                    new_status="completed",
                )
                return
            raise ProfileErasureServiceError("Profile erasure requires recovery.")
        profile_id = UUID(str(profile_id))

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
                self._require_runtime_cleanup(profile_id)
                await self._delete_historical_tavus(profile_id)
                snapshot = request.get("provider_snapshot") or {}
                await self.avatar_provider.delete_tavus_identity(
                    replica_id=snapshot.get("avatar_replica_id"),
                    persona_id=snapshot.get("avatar_persona_id"),
                )
                await self.voice_service.delete_profile_voice(
                    profile_id=profile_id
                )
                await self._delete_historical_voices(profile_id)
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
                # Recheck after storage cleanup: an in-flight provider response may
                # have recorded another voice while the earlier cleanup awaited I/O.
                await self._delete_historical_voices(profile_id)
                await self._delete_historical_tavus(profile_id)
                self._require_runtime_cleanup(profile_id)
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

    def _require_runtime_cleanup(self, profile_id: UUID) -> None:
        self.runtime_cleanup_repository.request_profile(profile_id)
        self.openai_realtime_registry.require_profile_terminated(profile_id)
        pending = self.runtime_cleanup_repository._execute(
            "SELECT 1 FROM avatar_runtime_cleanup WHERE profile_id=%s AND completed_at IS NULL LIMIT 1",
            (profile_id,),
        )
        if pending is not None:
            raise ProfileErasureServiceError("External session cleanup is still pending.")

    async def _delete_historical_voices(self, profile_id: UUID) -> None:
        jobs = self.repository.list_training_jobs(profile_id)
        for job in jobs:
            if job.get("training_type") != "voice" or job.get("status") == "deleted":
                continue
            voice_id = job.get("provider_job_id")
            if not voice_id:
                if job.get("status") in {"created", "submitted", "training"} or (
                    job.get("status") == "failed" and job.get("submitted_at") is not None
                ):
                    raise ProfileErasureServiceError("Voice creation requires reconciliation before deletion.")
                continue
            if job.get("provider") != "elevenlabs":
                raise ProfileErasureServiceError("Voice resource requires provider-specific cleanup.")
            await self.voice_service.delete_voice_resource(voice_id=str(voice_id))
            self.repository.update_training_job(UUID(str(job["job_id"])), status="deleted")

    async def _delete_historical_tavus(self, profile_id: UUID) -> None:
        for job in self.repository.list_training_jobs(profile_id):
            if job.get('training_type') != 'avatar' or job.get('status') == 'deleted':
                continue
            if job.get('provider') != 'tavus':
                raise ProfileErasureServiceError('Avatar resource requires provider-specific cleanup.')
            payload = job.get('provider_payload') or {}
            face_id = payload.get('face_id') or payload.get('faceId')
            replica_id = payload.get('replica_id')
            persona_id = payload.get('persona_id')
            if not face_id and not replica_id:
                if job.get('provider_job_id') or job.get('status') in {'created', 'submitted', 'training', 'ready'} or job.get('submitted_at'):
                    raise ProfileErasureServiceError('Avatar creation requires reconciliation before deletion.')
                continue
            if face_id:
                await self.avatar_provider.delete_tavus_face(face_id=str(face_id))
            if replica_id or persona_id:
                await self.avatar_provider.delete_tavus_identity(replica_id=replica_id, persona_id=persona_id)
            self.repository.update_training_job(UUID(str(job['job_id'])), status='deleted')
