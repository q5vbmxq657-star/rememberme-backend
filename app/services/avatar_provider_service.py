import os
from urllib.parse import urlsplit
import uuid
from dataclasses import dataclass
from typing import Optional, Dict, Any
from uuid import UUID

import httpx
from app.services.avatar_media_storage_service import AvatarMediaStorageService
from app.services.avatar_evidence_repository import AvatarEvidenceRepository, AvatarEvidenceRepositoryError

from app.services.digital_human_profile_repository import (
    DigitalHumanProfileNotFoundError,
    DigitalHumanProfileRepository,
    DigitalHumanProfileRepositoryError,
)


@dataclass
class AvatarProviderJobState:
    external_job_id: str
    external_avatar_id: Optional[str]
    status: str
    preview_url: Optional[str]
    error_message: Optional[str] = None


class AvatarProviderService:
    """
    Canonical server-side provider lifecycle for video avatar creation.

    The in-memory dictionary is a request-local cache only. PostgreSQL via
    digital_human_training_jobs and digital_human_profiles is the durable
    source of truth for Tavus replica training state.
    """

    def __init__(self) -> None:
        self._jobs: Dict[
            str,
            AvatarProviderJobState,
        ] = {}

        self._profile_repository: Optional[
            DigitalHumanProfileRepository
        ] = None

        self._media_storage_service: Optional[
            AvatarMediaStorageService
        ] = None

        self._evidence_repository: Optional[
            AvatarEvidenceRepository
        ] = None

    async def delete_tavus_identity(
        self,
        *,
        replica_id: str | None,
        persona_id: str | None,
    ) -> None:
        """Permanently remove profile-owned Tavus identity artifacts.

        Missing identifiers are already-clean state. Provider 404 responses are
        idempotent success; every other non-success response fails closed.
        """
        api_key = (os.getenv("TAVUS_API_KEY") or "").strip()
        identifiers = (
            ("personas", persona_id),
            ("replicas", replica_id),
        )

        if not any(value and value.strip() for _, value in identifiers):
            return
        if not api_key:
            raise RuntimeError("Tavus deletion is unavailable.")

        async with httpx.AsyncClient(timeout=30) as client:
            for resource, raw_identifier in identifiers:
                identifier = (raw_identifier or "").strip()
                if not identifier:
                    continue

                response = await client.delete(
                    f"https://tavusapi.com/v2/{resource}/{identifier}",
                    headers={"x-api-key": api_key},
                )
                if response.status_code not in {200, 204, 404}:
                    raise RuntimeError("Tavus deletion could not be verified.")

    async def submit(
        self,
        provider: str,
        profile_id: str,
        package_record_id: str,
        package: Dict[str, Any],
    ) -> AvatarProviderJobState:
        normalized_provider = provider.strip().lower()

        if normalized_provider == "tavus":
            return await self._submit_tavus_replica(
                profile_id=profile_id,
                package_record_id=package_record_id,
                package=package,
            )

        return AvatarProviderJobState(
            external_job_id=f"unsupported-provider-{uuid.uuid4()}",
            external_avatar_id=None,
            status="failed",
            preview_url=None,
            error_message=(
                "Unsupported video avatar provider. "
                "This backend currently supports Tavus for production avatar generation."
            ),
        )

    async def status(
        self,
        external_job_id: str,
    ) -> AvatarProviderJobState:
        cached = self._jobs.get(external_job_id)

        if external_job_id.startswith("tavus-video:"):
            return await self.fetch_tavus_video_status(external_job_id)

        if external_job_id.startswith("tavus:"):
            durable = self._load_tavus_training_state(
                external_job_id=external_job_id,
            )

            if durable is None:
                return AvatarProviderJobState(
                    external_job_id=external_job_id,
                    external_avatar_id=None,
                    status="failed",
                    preview_url=None,
                    error_message="Tavus provider job was not found in durable training state.",
                )

            return await self._fetch_tavus_status(durable)

        if cached is not None:
            return cached

        return AvatarProviderJobState(
            external_job_id=external_job_id,
            external_avatar_id=None,
            status="failed",
            preview_url=None,
            error_message="Provider job was not found.",
        )

    async def _submit_tavus_replica(
        self,
        profile_id: str,
        package_record_id: str,
        package: Dict[str, Any],
    ) -> AvatarProviderJobState:
        api_key = os.getenv("TAVUS_API_KEY")

        if not api_key:
            return AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="TAVUS_API_KEY is not configured.",
            )

        training_source = self._extract_tavus_training_source(
            package,
            profile_id=profile_id,
        )

        if training_source is None:
            return AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message=(
                    "No Tavus training source URL found in avatar package. "
                    "The package must contain one public HTTPS training image "
                    "or training video URL."
                ),
            )

        profile_uuid = self._parse_profile_id(profile_id)

        if profile_uuid is None:
            return AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="profile_id must be a valid UUID for Tavus avatar training.",
            )

        source_kind, source_url = training_source
        idempotency_key = f"avatar:tavus:{profile_id}:{package_record_id}"

        request_payload: Dict[str, Any] = {
            "package_record_id": package_record_id,
            source_kind: source_url,
            "model_name": "phoenix-4",
        }

        if source_kind == "train_image_url":
            voice_name = self._extract_tavus_image_voice_name(package)
            if not voice_name:
                return AvatarProviderJobState(
                    external_job_id=f"tavus:{uuid.uuid4()}",
                    external_avatar_id=None,
                    status="failed",
                    preview_url=None,
                    error_message=(
                        "Tavus image training requires a validated stock voice_name."
                    ),
                )
            request_payload["voice_name"] = voice_name
            request_payload["auto_fix_training_image"] = bool(
                package.get("auto_fix_training_image", True)
            )

        try:
            repository = self._repository()
            repository.ensure(profile_uuid)
            training_job = repository.create_training_job(
                job_id=uuid.uuid4(),
                profile_id=profile_uuid,
                training_type="avatar",
                provider="tavus",
                status="created",
                training_version=1,
                idempotency_key=idempotency_key,
                request_payload=request_payload,
            )
        except DigitalHumanProfileRepositoryError as error:
            return AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="Avatar training is temporarily unavailable.",
            )

        if not bool(training_job.get("was_created", False)):
            existing_external_job_id = str(
                training_job.get("provider_job_id") or ""
            ).strip()

            if existing_external_job_id:
                existing_state = self._load_tavus_training_state(
                    external_job_id=existing_external_job_id,
                )
                if existing_state is not None:
                    return existing_state

            return AvatarProviderJobState(
                external_job_id=f"tavus:pending:{training_job['job_id']}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="This avatar training request is already being submitted.",
            )

        payload: Dict[str, Any] = {
            "face_name": f"RememberMe-{profile_id[:8]}",
            "model_name": "phoenix-4",
            source_kind: source_url,
        }

        if source_kind == "train_image_url":
            payload["voice_name"] = request_payload["voice_name"]
            payload["auto_fix_training_image"] = request_payload[
                "auto_fix_training_image"
            ]

        callback_url = os.getenv("TAVUS_CALLBACK_URL")
        webhook_secret = os.getenv("TAVUS_WEBHOOK_SECRET")

        if callback_url:
            if webhook_secret and "secret=" not in callback_url:
                separator = "&" if "?" in callback_url else "?"
                callback_url = f"{callback_url}{separator}secret={webhook_secret}"

            payload["callback_url"] = callback_url

        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                "https://tavusapi.com/v2/faces",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                },
                json=payload,
            )

        if response.status_code >= 400:
            normalized_error = self._normalize_tavus_error(
                status_code=response.status_code,
                response_text=response.text,
            )

            self._mark_tavus_training_failed(
                job_id=training_job["job_id"],
                profile_id=profile_uuid,
                provider_job_id=None,
                error_message=normalized_error,
                provider_payload={
                    "status_code": response.status_code,
                },
            )

            return AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message=normalized_error,
            )

        data = response.json()
        replica_id = (
            data.get("face_id")
            or data.get("replica_id")
            or data.get("id")
        )

        if not replica_id:
            error_message = "Tavus response did not include face_id."

            self._mark_tavus_training_failed(
                job_id=training_job["job_id"],
                profile_id=profile_uuid,
                provider_job_id=None,
                error_message=error_message,
                provider_payload=data,
            )

            return AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message=error_message,
            )

        external_job_id = f"tavus:{replica_id}"

        self._mark_tavus_training_submitted(
            job_id=training_job["job_id"],
            profile_id=profile_uuid,
            provider_job_id=external_job_id,
            replica_id=replica_id,
            provider_payload=data,
        )

        state = AvatarProviderJobState(
            external_job_id=external_job_id,
            external_avatar_id=replica_id,
            status="training",
            preview_url=None,
        )
        self._jobs[state.external_job_id] = state
        return state

    async def _fetch_tavus_status(
        self,
        existing: AvatarProviderJobState,
    ) -> AvatarProviderJobState:
        api_key = os.getenv("TAVUS_API_KEY")

        if not api_key:
            existing.status = "failed"
            existing.error_message = "TAVUS_API_KEY is not configured."
            return existing

        replica_id = existing.external_avatar_id

        if not replica_id:
            existing.status = "failed"
            existing.error_message = "Missing Tavus replica id."
            return existing

        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(
                f"https://tavusapi.com/v2/faces/{replica_id}",
                headers={"x-api-key": api_key},
            )

        if response.status_code >= 400:
            existing.status = "failed"
            existing.error_message = (
                "Avatar training status is temporarily unavailable."
            )
            self._sync_tavus_status_to_profile(
                state=existing,
                provider_payload={
                    "status_code": response.status_code,
                },
            )
            return existing

        data = response.json()
        tavus_status = str(data.get("status", "")).lower()
        error_message = data.get("error_message") or data.get("error")

        if tavus_status in {"ready", "completed", "complete"}:
            existing.status = "ready"
            existing.preview_url = None
            existing.error_message = None
        elif tavus_status in {"failed", "error"}:
            existing.status = "failed"
            existing.error_message = "Avatar training did not complete."
        else:
            existing.status = "training"
            existing.error_message = None

        self._sync_tavus_status_to_profile(
            state=existing,
            provider_payload=data,
        )

        self._jobs[existing.external_job_id] = existing
        return existing

    async def create_tavus_video(
        self,
        profile_id: UUID,
        replica_id: str,
        script: str,
    ) -> AvatarProviderJobState:
        normalized_replica_id = (
            replica_id.strip()
        )

        normalized_script = (
            script.strip()
        )

        provisional_job_id = (
            uuid.uuid4()
        )

        provisional_external_id = (
            "tavus-video:"
            + str(
                provisional_job_id
            )
        )

        if not normalized_replica_id:
            return AvatarProviderJobState(
                external_job_id=(
                    provisional_external_id
                ),
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message=(
                    "replica_id is required."
                ),
            )

        if not normalized_script:
            return AvatarProviderJobState(
                external_job_id=(
                    provisional_external_id
                ),
                external_avatar_id=(
                    normalized_replica_id
                ),
                status="failed",
                preview_url=None,
                error_message=(
                    "script is required."
                ),
            )

        try:
            binding = (
                self._repository()
                .resolve_ready_avatar_by_replica(
                    provider="tavus",
                    replica_id=(
                        normalized_replica_id
                    ),
                )
            )
        except (
            DigitalHumanProfileRepositoryError,
            ValueError,
        ) as error:
            return AvatarProviderJobState(
                external_job_id=(
                    provisional_external_id
                ),
                external_avatar_id=(
                    normalized_replica_id
                ),
                status="failed",
                preview_url=None,
                error_message=(
                    "Avatar verification is temporarily unavailable."
                ),
            )

        if binding is None:
            return AvatarProviderJobState(
                external_job_id=(
                    provisional_external_id
                ),
                external_avatar_id=(
                    normalized_replica_id
                ),
                status="failed",
                preview_url=None,
                error_message=(
                    "The requested replica is not "
                    "bound to a current ready "
                    "STAY avatar profile."
                ),
            )

        if UUID(str(binding["profile_id"])) != profile_id:
            return AvatarProviderJobState(
                external_job_id=provisional_external_id,
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="The requested avatar was not found.",
            )

        preview_job_id = (
            uuid.uuid4()
        )

        try:
            self._repository().create_generated_preview_job(
                job_id=preview_job_id,
                profile_id=binding[
                    "profile_id"
                ],
                training_version=int(
                    binding[
                        "training_version"
                    ]
                ),
                package_record_id=str(
                    binding[
                        "package_record_id"
                    ]
                ),
                provider="tavus",
                replica_id=(
                    normalized_replica_id
                ),
            )
        except (
            DigitalHumanProfileRepositoryError,
            ValueError,
        ) as error:
            return AvatarProviderJobState(
                external_job_id=(
                    "tavus-video:"
                    + str(
                        preview_job_id
                    )
                ),
                external_avatar_id=(
                    normalized_replica_id
                ),
                status="failed",
                preview_url=None,
                error_message=(
                    "Avatar preview creation is temporarily unavailable."
                ),
            )

        api_key = os.getenv(
            "TAVUS_API_KEY"
        )

        if not api_key:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="failed",
                error_code=(
                    "tavus_api_key_missing"
                ),
                error_message=(
                    "Avatar training is temporarily unavailable."
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    "tavus-video:"
                    + str(
                        preview_job_id
                    )
                ),
                external_avatar_id=(
                    normalized_replica_id
                ),
                status="failed",
                preview_url=None,
                error_message=(
                    "TAVUS_API_KEY is not configured."
                ),
            )

        try:
            async with httpx.AsyncClient(
                timeout=90
            ) as client:
                response = await client.post(
                    (
                        "https://tavusapi.com/"
                        "v2/videos"
                    ),
                    headers={
                        "Content-Type":
                            "application/json",
                        "x-api-key":
                            api_key,
                    },
                    json={
                        "replica_id":
                            normalized_replica_id,
                        "script":
                            normalized_script,
                    },
                )
        except httpx.HTTPError as error:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="failed",
                error_code=(
                    "tavus_submission_transport_error"
                ),
                error_message=str(
                    error
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    "tavus-video:"
                    + str(
                        preview_job_id
                    )
                ),
                external_avatar_id=(
                    normalized_replica_id
                ),
                status="failed",
                preview_url=None,
                error_message=(
                    "Tavus video submission failed."
                ),
            )

        if response.status_code >= 400:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="failed",
                provider_payload={
                    "status_code":
                        response.status_code,
                },
                error_code=(
                    "tavus_submission_rejected"
                ),
                error_message=(
                    "Tavus rejected the "
                    "generated-preview request."
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    "tavus-video:"
                    + str(
                        preview_job_id
                    )
                ),
                external_avatar_id=(
                    normalized_replica_id
                ),
                status="failed",
                preview_url=None,
                error_message=(
                    "Tavus rejected the "
                    "generated-preview request."
                ),
            )

        data = response.json()

        video_id = str(
            data.get(
                "video_id"
            )
            or data.get(
                "id"
            )
            or ""
        ).strip()

        if not video_id:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="failed",
                provider_payload=data,
                error_code=(
                    "tavus_video_id_missing"
                ),
                error_message=(
                    "Tavus response did not "
                    "include video_id."
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    "tavus-video:"
                    + str(
                        preview_job_id
                    )
                ),
                external_avatar_id=(
                    normalized_replica_id
                ),
                status="failed",
                preview_url=None,
                error_message=(
                    "Tavus response did not "
                    "include video_id."
                ),
            )

        self._repository().update_generated_preview_job(
            job_id=preview_job_id,
            status="generating",
            provider_video_id=(
                video_id
            ),
            provider_payload=data,
        )

        state = AvatarProviderJobState(
            external_job_id=(
                f"tavus:video:{video_id}"
            ),
            external_avatar_id=(
                normalized_replica_id
            ),
            status="generatingPreview",
            preview_url=None,
            error_message=None,
        )

        self._jobs[
            state.external_job_id
        ] = state

        return state

    async def fetch_tavus_video_status(
        self,
        external_job_id: str,
        expected_profile_id: UUID | None = None,
    ) -> AvatarProviderJobState:
        normalized_external_job_id = (
            external_job_id.strip()
        )

        if not normalized_external_job_id:
            return AvatarProviderJobState(
                external_job_id=external_job_id,
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message=(
                    "external_job_id is required."
                ),
            )

        try:
            durable_job = (
                self._repository()
                .get_generated_preview_job_by_external_id(
                    provider="tavus",
                    external_job_id=(
                        normalized_external_job_id
                    ),
                )
            )
        except (
            DigitalHumanProfileRepositoryError,
            ValueError,
        ) as error:
            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message=(
                    "Avatar preview status is temporarily unavailable."
                ),
            )

        if durable_job is None:
            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message=(
                    "Generated-preview job was not "
                    "found in durable STAY state."
                ),
            )

        preview_job_id = durable_job[
            "job_id"
        ]

        profile_id = UUID(
            str(
                durable_job[
                    "profile_id"
                ]
            )
        )

        if (
            expected_profile_id is not None
            and profile_id != expected_profile_id
        ):
            return AvatarProviderJobState(
                external_job_id=normalized_external_job_id,
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="Generated-preview job was not found.",
            )

        replica_id = str(
            durable_job.get(
                "replica_id"
            )
            or ""
        ).strip()

        generated_asset_id = (
            durable_job.get(
                "generated_asset_id"
            )
        )


        provider_video_id = str(
            durable_job.get(
                "provider_video_id"
            )
            or ""
        ).strip()

        current_training_version = int(
            durable_job.get(
                "current_training_version"
            )
            or 0
        )

        bound_training_version = int(
            durable_job.get(
                "training_version"
            )
            or 0
        )

        current_replica_id = str(
            durable_job.get(
                "current_replica_id"
            )
            or ""
        ).strip()

        current_avatar_status = str(
            durable_job.get(
                "current_avatar_status"
            )
            or ""
        ).strip().lower()

        binding_is_current = (
            current_training_version
            == bound_training_version
            and
            bool(replica_id)
            and
            replica_id == current_replica_id
            and
            current_avatar_status == "ready"
        )

        if not binding_is_current:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="stale",
                error_code="avatar_binding_stale",
                error_message=(
                    "The profile, training version "
                    "or replica binding changed "
                    "after preview submission."
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=(
                    replica_id or None
                ),
                status="failed",
                preview_url=None,
                error_message=(
                    "Generated preview is stale "
                    "because the avatar was updated."
                ),
            )
        if (
            str(
                durable_job.get(
                    "status"
                )
                or ""
            ).strip().lower()
            == "ready"
            and
            generated_asset_id
        ):
            signed = (
                self._media_storage()
                .sign_download_url(
                    asset_id=str(
                        generated_asset_id
                    ),
                    expires_in_seconds=900,
                )
            )

            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=(
                    replica_id or None
                ),
                status="ready",
                preview_url=(
                    signed.signed_url
                ),
                error_message=None,
            )


        if not provider_video_id:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="failed",
                error_code=(
                    "provider_video_id_missing"
                ),
                error_message=(
                    "Durable preview state has no "
                    "provider video identifier."
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=replica_id,
                status="failed",
                preview_url=None,
                error_message=(
                    "Provider video identifier "
                    "is missing."
                ),
            )

        api_key = os.getenv(
            "TAVUS_API_KEY"
        )

        if not api_key:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="failed",
                error_code=(
                    "tavus_api_key_missing"
                ),
                error_message=(
                    "TAVUS_API_KEY is not configured."
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=replica_id,
                status="failed",
                preview_url=None,
                error_message=(
                    "TAVUS_API_KEY is not configured."
                ),
            )

        try:
            async with httpx.AsyncClient(
                timeout=45
            ) as client:
                response = await client.get(
                    (
                        "https://tavusapi.com/"
                        "v2/videos/"
                        + provider_video_id
                    ),
                    headers={
                        "x-api-key": api_key,
                    },
                )
        except httpx.HTTPError as error:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="generating",
                error_code=(
                    "tavus_poll_transport_error"
                ),
                error_message=str(
                    error
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=replica_id,
                status="generatingPreview",
                preview_url=None,
                error_message=(
                    "Preview status is temporarily "
                    "unavailable."
                ),
            )

        if response.status_code >= 400:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="generating",
                provider_payload={
                    "poll_status_code":
                        response.status_code,
                },
                error_code="tavus_poll_rejected",
                error_message=(
                    "Tavus rejected the preview "
                    "status request."
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=replica_id,
                status="generatingPreview",
                preview_url=None,
                error_message=(
                    "Preview status is temporarily "
                    "unavailable."
                ),
            )

        data = response.json()

        raw_status = str(
            data.get(
                "status"
            )
            or ""
        ).strip().lower()

        completed_statuses = {
            "completed",
            "complete",
            "ready",
            "success",
            "succeeded",
        }

        failed_statuses = {
            "failed",
            "error",
            "rejected",
            "deleted",
        }

        cancelled_statuses = {
            "cancelled",
            "canceled",
        }

        if raw_status in completed_statuses:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="materializing",
                provider_payload=data,
                error_code=None,
                error_message=None,
            )

            materialized_asset_id: Optional[UUID] = None
            evidence_persisted = False

            try:
                source_url = (
                    self
                    ._generated_preview_download_url(
                        data
                    )
                )

                upload, media_sha256 = (
                    await self._media_storage()
                    .ingest_remote_generated_preview(
                        profile_id=str(
                            profile_id
                        ),
                        title=(
                            "Generated avatar preview"
                        ),
                        source_url=source_url,
                        upload_id=str(
                            preview_job_id
                        ),
                    )
                )

                materialized_asset_id = UUID(
                    str(upload.asset_id)
                )

                metadata = (
                    self._media_storage()
                    .get_metadata(
                        str(
                            materialized_asset_id
                        )
                    )
                )

                evidence = (
                    self._evidence()
                    .upsert_uploaded_asset(
                        asset_id=(
                            materialized_asset_id
                        ),
                        profile_id=profile_id,
                        asset_type=(
                            "generated_preview"
                        ),
                        evidence_kind=(
                            "generated_preview"
                        ),
                        title=metadata.title,
                        filename=metadata.filename,
                        content_type=(
                            metadata.content_type
                        ),
                        size_bytes=(
                            metadata.size_bytes
                        ),
                        storage_backend=(
                            "local_private"
                        ),
                        storage_key=(
                            f"{metadata.profile_id}/"
                            f"{metadata.filename}"
                        ),
                        storage_path=(
                            metadata.storage_path
                        ),
                        quality_score=0.0,
                        has_face=False,
                        has_frontal_face=False,
                        has_clear_lighting=False,
                        has_voice=False,
                        voice_usable=False,
                        motion_usable=False,
                        emotional_presence_score=0.0,
                        identity_consistency_score=0.0,
                        motion_quality_score=0.0,
                        expression_range_score=0.0,
                        lip_visibility_score=0.0,
                        head_pose_stability_score=0.0,
                        recommended_for_avatar=False,
                        analysis_version=(
                            "generated-preview-evidence-v1"
                        ),
                        analysis_metadata={
                            "biometric_evaluation":
                                "not_performed",
                            "quality_evaluation":
                                "not_performed",
                            "identity_verification":
                                "not_performed",
                        },
                        source_metadata={
                            "provider": "tavus",
                            "provider_video_id":
                                provider_video_id,
                            "generated_preview_job_id":
                                str(
                                    preview_job_id
                                ),
                            "training_version":
                                bound_training_version,
                            "package_record_id":
                                str(
                                    durable_job.get(
                                        "package_record_id"
                                    )
                                    or ""
                                ),
                            "media_sha256":
                                media_sha256,
                        },
                    )
                )

                evidence_persisted = True

                signed = (
                    self._media_storage()
                    .sign_download_url(
                        asset_id=str(
                            evidence.asset_id
                        ),
                        expires_in_seconds=900,
                    )
                )

                self._repository().update_generated_preview_job(
                    job_id=preview_job_id,
                    status="ready",
                    provider_payload=data,
                    generated_asset_id=(
                        evidence.asset_id
                    ),
                    media_sha256=(
                        media_sha256
                    ),
                    media_content_type=(
                        metadata.content_type
                    ),
                    media_size_bytes=(
                        metadata.size_bytes
                    ),
                    error_code=None,
                    error_message=None,
                )

                return AvatarProviderJobState(
                    external_job_id=(
                        normalized_external_job_id
                    ),
                    external_avatar_id=(
                        replica_id
                    ),
                    status="ready",
                    preview_url=(
                        signed.signed_url
                    ),
                    error_message=None,
                )

            except (
                AvatarEvidenceRepositoryError,
                DigitalHumanProfileRepositoryError,
                RuntimeError,
                ValueError,
            ) as error:
                cleanup_errors: list[str] = []

                if materialized_asset_id is not None:
                    cleanup_errors = (
                        self
                        ._compensate_generated_preview_materialization(
                            profile_id=profile_id,
                            asset_id=(
                                materialized_asset_id
                            ),
                            evidence_persisted=(
                                evidence_persisted
                            ),
                        )
                    )

                diagnostic = (
                    "generated_preview_materialization_failed:"
                    f"{type(error).__name__}"
                )

                if cleanup_errors:
                    diagnostic += (
                        ";"
                        + ";".join(
                            cleanup_errors
                        )
                    )

                try:
                    self._repository().update_generated_preview_job(
                        job_id=preview_job_id,
                        status="materializing",
                        provider_payload=data,
                        error_code=(
                            "generated_preview_"
                            "materialization_failed"
                        ),
                        error_message=diagnostic,
                    )
                except (
                    DigitalHumanProfileRepositoryError,
                    RuntimeError,
                    ValueError,
                ):
                    pass

                return AvatarProviderJobState(
                    external_job_id=(
                        normalized_external_job_id
                    ),
                    external_avatar_id=(
                        replica_id
                    ),
                    status="generatingPreview",
                    preview_url=None,
                    error_message=(
                        "Generated preview is being "
                        "secured for private playback."
                    ),
                )

        if raw_status in failed_statuses:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="failed",
                provider_payload=data,
                error_code=(
                    "tavus_generation_failed"
                ),
                error_message=(
                    "Tavus could not generate "
                    "the preview video."
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=replica_id,
                status="failed",
                preview_url=None,
                error_message=(
                    "Avatar preview generation failed."
                ),
            )

        if raw_status in cancelled_statuses:
            self._repository().update_generated_preview_job(
                job_id=preview_job_id,
                status="cancelled",
                provider_payload=data,
                error_code=(
                    "tavus_generation_cancelled"
                ),
                error_message=(
                    "Tavus preview generation "
                    "was cancelled."
                ),
            )

            return AvatarProviderJobState(
                external_job_id=(
                    normalized_external_job_id
                ),
                external_avatar_id=replica_id,
                status="failed",
                preview_url=None,
                error_message=(
                    "Avatar preview generation "
                    "was cancelled."
                ),
            )

        self._repository().update_generated_preview_job(
            job_id=preview_job_id,
            status="generating",
            provider_payload=data,
            error_code=None,
            error_message=None,
        )

        return AvatarProviderJobState(
            external_job_id=(
                normalized_external_job_id
            ),
            external_avatar_id=replica_id,
            status="generatingPreview",
            preview_url=None,
            error_message=None,
        )

    def _media_storage(
        self,
    ) -> AvatarMediaStorageService:
        if self._media_storage_service is None:
            self._media_storage_service = (
                AvatarMediaStorageService()
            )

        return self._media_storage_service

    def _evidence(
        self,
    ) -> AvatarEvidenceRepository:
        if self._evidence_repository is None:
            self._evidence_repository = (
                AvatarEvidenceRepository()
            )

        return self._evidence_repository

    @staticmethod
    def _generated_preview_download_url(
        payload: Dict[str, Any],
    ) -> str:
        source_url = str(
            payload.get(
                "download_url"
            )
            or ""
        ).strip()

        if not source_url:
            raise RuntimeError(
                "Ready Tavus video has no "
                "download_url."
            )

        parsed = urlsplit(
            source_url
        )

        if (
            parsed.scheme.lower() != "https"
            or
            not parsed.hostname
        ):
            raise RuntimeError(
                "Tavus download_url must be "
                "an absolute HTTPS URL."
            )

        return source_url

    def _compensate_generated_preview_materialization(
        self,
        *,
        profile_id: UUID,
        asset_id: UUID,
        evidence_persisted: bool,
    ) -> list[str]:
        """
        Remove partial generated-preview state using
        profile-bound Evidence and Storage operations.

        Evidence is removed first. Physical media is
        then deleted only when Storage metadata proves
        the same profile ownership.
        """

        cleanup_errors: list[str] = []

        if evidence_persisted:
            try:
                self._evidence().delete_generated_preview_asset(
                    profile_id=profile_id,
                    asset_id=asset_id,
                )
            except Exception as error:
                cleanup_errors.append(
                    "evidence_cleanup_failed:"
                    f"{type(error).__name__}"
                )

        try:
            self._media_storage().delete_profile_asset(
                profile_id=str(
                    profile_id
                ),
                asset_id=str(
                    asset_id
                ),
            )
        except Exception as error:
            cleanup_errors.append(
                "storage_cleanup_failed:"
                f"{type(error).__name__}"
            )

        return cleanup_errors

    def _repository(self) -> DigitalHumanProfileRepository:
        if self._profile_repository is None:
            self._profile_repository = DigitalHumanProfileRepository()

        return self._profile_repository

    def _load_tavus_training_state(
        self,
        *,
        external_job_id: str,
    ) -> Optional[AvatarProviderJobState]:
        try:
            job = self._repository().get_training_job_by_provider_job_id(
                provider="tavus",
                provider_job_id=external_job_id,
            )
        except DigitalHumanProfileRepositoryError:
            return None

        if not job:
            return None

        provider_payload = dict(job.get("provider_payload") or {})
        replica_id = (
            provider_payload.get("face_id")
            or provider_payload.get("faceId")
            or provider_payload.get("replica_id")
            or provider_payload.get("id")
            or external_job_id.replace("tavus:", "")
        )

        return AvatarProviderJobState(
            external_job_id=external_job_id,
            external_avatar_id=replica_id,
            status=str(job["status"]),
            preview_url=None,
            error_message=job.get("error_message"),
        )

    def _mark_tavus_training_submitted(
        self,
        *,
        job_id: UUID,
        profile_id: UUID,
        provider_job_id: str,
        replica_id: str,
        provider_payload: Dict[str, Any],
    ) -> None:
        repository = self._repository()

        repository.update_training_job(
            job_id,
            status="training",
            provider_job_id=provider_job_id,
            provider_payload=provider_payload,
        )

        repository.set_avatar_training(
            profile_id,
            provider="tavus",
            status="training",
            provider_job_id=provider_job_id,
            replica_id=replica_id,
            error_code=None,
            error_message=None,
        )

    def _mark_tavus_training_failed(
        self,
        *,
        job_id: UUID,
        profile_id: UUID,
        provider_job_id: Optional[str],
        error_message: str,
        provider_payload: Dict[str, Any],
    ) -> None:
        try:
            repository = self._repository()

            repository.update_training_job(
                job_id,
                status="failed",
                provider_job_id=provider_job_id,
                provider_payload=provider_payload,
                error_code="tavus_provider_failed",
                error_message=error_message,
            )

            repository.set_avatar_training(
                profile_id,
                provider="tavus",
                status="failed",
                provider_job_id=provider_job_id,
                replica_id=None,
                error_code="tavus_provider_failed",
                error_message=error_message,
            )
        except (
            DigitalHumanProfileRepositoryError,
            DigitalHumanProfileNotFoundError,
        ):
            return

    def _sync_tavus_status_to_profile(
        self,
        *,
        state: AvatarProviderJobState,
        provider_payload: Dict[str, Any],
    ) -> None:
        if not state.external_job_id.startswith("tavus:"):
            return

        try:
            repository = self._repository()
            job = repository.get_training_job_by_provider_job_id(
                provider="tavus",
                provider_job_id=state.external_job_id,
            )

            if not job:
                return

            profile_id = job["profile_id"]

            repository.update_training_job(
                job["job_id"],
                status=state.status,
                provider_job_id=state.external_job_id,
                provider_payload=provider_payload,
                error_code="tavus_provider_failed" if state.status == "failed" else None,
                error_message=state.error_message,
            )

            repository.set_avatar_training(
                profile_id,
                provider="tavus",
                status=state.status,
                provider_job_id=state.external_job_id,
                replica_id=state.external_avatar_id,
                error_code="tavus_provider_failed" if state.status == "failed" else None,
                error_message=state.error_message,
            )
        except (
            DigitalHumanProfileRepositoryError,
            DigitalHumanProfileNotFoundError,
        ):
            return

    def apply_tavus_webhook(
        self,
        payload: Dict[str, Any],
    ) -> AvatarProviderJobState:
        replica_id = self._extract_tavus_replica_id_from_payload(payload)

        if not replica_id:
            return AvatarProviderJobState(
                external_job_id="tavus:unknown",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="Tavus webhook payload did not include a replica id.",
            )

        external_job_id = f"tavus:{replica_id}"
        existing = self._load_tavus_training_state(
            external_job_id=external_job_id,
        )

        if existing is None:
            return AvatarProviderJobState(
                external_job_id=external_job_id,
                external_avatar_id=replica_id,
                status="failed",
                preview_url=None,
                error_message="Tavus webhook was received for an unknown durable training job.",
            )

        normalized_status = self._normalize_tavus_status_from_payload(payload)
        error_message = self._extract_tavus_error_from_payload(payload)

        if normalized_status == "ready":
            state = AvatarProviderJobState(
                external_job_id=external_job_id,
                external_avatar_id=replica_id,
                status="ready",
                preview_url=None,
                error_message=None,
            )
        elif normalized_status == "failed":
            state = AvatarProviderJobState(
                external_job_id=external_job_id,
                external_avatar_id=replica_id,
                status="failed",
                preview_url=None,
                error_message=error_message or "Tavus replica training failed.",
            )
        else:
            state = AvatarProviderJobState(
                external_job_id=external_job_id,
                external_avatar_id=replica_id,
                status="training",
                preview_url=None,
                error_message=None,
            )

        self._sync_tavus_status_to_profile(
            state=state,
            provider_payload=payload,
        )

        self._jobs[state.external_job_id] = state
        return state

    def _extract_tavus_replica_id_from_payload(
        self,
        payload: Dict[str, Any],
    ) -> Optional[str]:
        direct_candidates = [
            payload.get("face_id"),
            payload.get("faceId"),
            payload.get("replica_id"),
            payload.get("replicaId"),
            payload.get("id"),
        ]

        for candidate in direct_candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        nested_keys = [
            "replica",
            "data",
            "payload",
        ]

        for key in nested_keys:
            nested = payload.get(key)

            if not isinstance(nested, dict):
                continue

            for nested_key in [
                "face_id",
                "faceId",
                "replica_id",
                "replicaId",
                "id",
            ]:
                candidate = nested.get(nested_key)

                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()

        return None

    def _normalize_tavus_status_from_payload(
        self,
        payload: Dict[str, Any],
    ) -> str:
        status_value = (
            payload.get("status")
            or payload.get("replica_status")
            or payload.get("replicaStatus")
        )

        if not status_value and isinstance(payload.get("data"), dict):
            status_value = payload["data"].get("status")

        normalized = str(status_value or "").strip().lower()

        if normalized in {"ready", "completed", "complete", "success"}:
            return "ready"

        if normalized in {"failed", "error", "cancelled", "canceled"}:
            return "failed"

        return "training"

    def _extract_tavus_error_from_payload(
        self,
        payload: Dict[str, Any],
    ) -> Optional[str]:
        direct_candidates = [
            payload.get("error_message"),
            payload.get("errorMessage"),
            payload.get("error"),
            payload.get("status_details"),
            payload.get("statusDetails"),
        ]

        for candidate in direct_candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        data = payload.get("data")

        if isinstance(data, dict):
            for key in [
                "error_message",
                "errorMessage",
                "error",
                "status_details",
                "statusDetails",
            ]:
                candidate = data.get(key)

                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()

        return None


    def _parse_profile_id(
        self,
        profile_id: str,
    ) -> Optional[UUID]:
        try:
            return UUID(profile_id)
        except ValueError:
            return None

    def _normalize_tavus_error(
        self,
        status_code: int,
        response_text: str,
    ) -> str:
        lower = response_text.lower()

        if status_code == 402 or "payment required" in lower:
            return (
                "Avatar training is temporarily unavailable."
            )

        if status_code == 401 or "unauthorized" in lower:
            return (
                "Avatar training is temporarily unavailable."
            )

        if status_code == 403 or "forbidden" in lower:
            return (
                "Avatar training is temporarily unavailable."
            )

        if ("video" in lower or "image" in lower) and (
            "url" in lower or "invalid" in lower
        ):
            return (
                "This media could not be used for avatar training."
            )

        return "Avatar training could not be started. Please try again."

    def _extract_tavus_training_video_url(
        self,
        package: Dict[str, Any],
        *,
        profile_id: Optional[str] = None,
    ) -> Optional[str]:
        direct_keys = [
            "train_video_url",
            "trainVideoURL",
            "motionVideoURL",
            "primaryMotionVideoURL",
            "videoURL",
            "remoteVideoURL",
        ]

        for key in direct_keys:
            value = package.get(key)
            if isinstance(value, str) and value.startswith("https://"):
                return value

        asset_collections = [
            package.get("motionVideos"),
            package.get("motion_videos"),
            package.get("assets"),
        ]
        for assets in asset_collections:
            if not isinstance(assets, list):
                continue
            for asset in assets:
                if not isinstance(asset, dict):
                    continue

                kind = str(asset.get("type") or asset.get("kind") or asset.get("assetType") or "").lower()
                if "video" not in kind and assets is package.get("assets"):
                    continue

                url = self._provider_training_url_from_asset(
                    asset,
                    profile_id=profile_id,
                )
                if url:
                    return url

        return None

    def _extract_tavus_training_image_url(
        self,
        package: Dict[str, Any],
        *,
        profile_id: Optional[str] = None,
    ) -> Optional[str]:
        direct_keys = [
            "train_image_url",
            "trainImageURL",
            "identityPhotoURL",
            "primaryIdentityPhotoURL",
            "imageURL",
            "remoteImageURL",
        ]

        for key in direct_keys:
            value = package.get(key)
            if isinstance(value, str) and value.startswith("https://"):
                return value

        asset_collections = [
            package.get("identityPhotos"),
            package.get("identity_photos"),
            package.get("assets"),
        ]
        for assets in asset_collections:
            if not isinstance(assets, list):
                continue
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                kind = str(
                    asset.get("type")
                    or asset.get("kind")
                    or asset.get("assetType")
                    or ""
                ).lower()
                if (
                    "image" not in kind
                    and "photo" not in kind
                    and assets is package.get("assets")
                ):
                    continue

                url = self._provider_training_url_from_asset(
                    asset,
                    profile_id=profile_id,
                )
                if url:
                    return url
        return None

    def _extract_tavus_training_source(
        self,
        package: Dict[str, Any],
        *,
        profile_id: Optional[str] = None,
    ) -> Optional[tuple[str, str]]:
        requested_mode = str(
            package.get("tavus_training_mode")
            or package.get("tavusTrainingMode")
            or ""
        ).strip().lower()
        image_url = self._extract_tavus_training_image_url(
            package,
            profile_id=profile_id,
        )
        video_url = self._extract_tavus_training_video_url(
            package,
            profile_id=profile_id,
        )

        if requested_mode == "image":
            return ("train_image_url", image_url) if image_url else None
        if requested_mode == "video":
            return ("train_video_url", video_url) if video_url else None
        if video_url:
            return "train_video_url", video_url
        if image_url:
            return "train_image_url", image_url
        return None

    def _provider_training_url_from_asset(
        self,
        asset: Dict[str, Any],
        *,
        profile_id: Optional[str],
    ) -> Optional[str]:
        remote_asset_id = (
            asset.get("remoteAssetID")
            or asset.get("remoteAssetId")
            or asset.get("remote_asset_id")
            or asset.get("assetID")
            or asset.get("assetId")
            or asset.get("asset_id")
        )

        if profile_id and isinstance(remote_asset_id, str):
            normalized_asset_id = remote_asset_id.strip()
            if normalized_asset_id:
                try:
                    return self._media_storage().sign_provider_training_url(
                        asset_id=normalized_asset_id,
                        profile_id=profile_id,
                    ).signed_url
                except (RuntimeError, ValueError, FileNotFoundError):
                    return None

        remote_url = (
            asset.get("remoteURL")
            or asset.get("remoteUrl")
            or asset.get("remote_url")
            or asset.get("url")
            or asset.get("downloadURL")
            or asset.get("downloadUrl")
        )
        if isinstance(remote_url, str) and remote_url.startswith("https://"):
            return remote_url
        return None

    @staticmethod
    def _extract_tavus_image_voice_name(
        package: Dict[str, Any],
    ) -> Optional[str]:
        value = (
            package.get("voice_name")
            or package.get("voiceName")
            or os.getenv("TAVUS_IMAGE_TRAINING_VOICE_NAME")
        )
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        return normalized or None


avatar_provider_service = AvatarProviderService()
