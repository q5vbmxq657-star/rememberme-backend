from __future__ import annotations
from app.security.purpose_authorization import require_profile_purposes

import hashlib
import os
import asyncio
from tempfile import NamedTemporaryFile

from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional
from uuid import UUID, uuid4

import httpx
from app.schemas.voice_delivery import VoiceDelivery
from app.services.avatar_media_analysis_service import (
    AvatarMediaAnalysisError,
    AvatarMediaAnalysisUnavailableError,
    AvatarMediaAnalysisService,
)

from app.models.digital_human_profile import (
    DigitalHumanProfile,
)
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileNotFoundError,
    DigitalHumanProfileRepository,
    DigitalHumanProfileRepositoryError,
    StaleVoiceTrainingError,
)


class ElevenLabsVoiceError(RuntimeError):
    pass


class ElevenLabsVoiceValidationError(
    ElevenLabsVoiceError
):
    @property
    def safe_user_message(self) -> str:
        """Present only reviewed validation causes, never raw diagnostics."""
        message = str(self)
        known = {
            "Voice cloning requires verified consent.": "Confirm permission to use this person's voice before training.",
            "A display name is required.": "Enter a name for this voice.",
            "An idempotency key is required.": "Please start a new voice upload and try again.",
            "At least one voice sample is required.": "Add at least one voice recording.",
            "Too many voice samples.": "Too many recordings. Please select fewer files.",
            "A voice sample has no filename.": "Choose a recording with a valid filename.",
            "A voice sample is too small.": "One recording is too short. Choose a longer recording.",
            "Voice samples are too small.": "Add longer recordings for voice training.",
            "A voice sample exceeds the size limit.": "One recording is too large. Choose a shorter recording.",
            "Voice samples exceed the total size limit.": "These recordings are too large. Please use shorter recordings.",
            "The selected file contains no audio track.": "This file has no audio track. Choose a voice recording.",
            "The selected recording could not be decoded.": "This recording could not be read. Export it again or choose another recording.",
            "The selected recording contains no readable audio.": "This recording contains no readable audio. Choose another voice recording.",
            "Use a clear recording of at least three seconds without clipping.": "Use a clear recording of at least three seconds without clipping.",
        }
        if message.startswith("Unsupported voice sample type: "):
            return "This recording format is not supported. Choose an audio recording in a supported format."
        return known.get(message, "This recording cannot be used for voice training.")


class ElevenLabsVoiceProviderError(
    ElevenLabsVoiceError
):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        provider_code: Optional[str] = None,
        created_voice_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = provider_code
        self.created_voice_id = created_voice_id

    @property
    def is_capacity_unavailable(self) -> bool:
        code = (self.provider_code or "").lower()
        return any(
            marker in code
            for marker in (
                "quota",
                "limit",
                "capacity",
                "subscription",
                "voice_slot",
            )
        )


class ElevenLabsVoiceConflictError(
    ElevenLabsVoiceError
):
    pass


@dataclass(frozen=True)
class VoiceCloneSample:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class VoiceCloneResult:
    job_id: UUID
    profile_id: UUID
    voice_id: str
    status: str
    requires_verification: bool


@dataclass(frozen=True)
class VoiceSynthesisResult:
    audio_stream: BytesIO
    voice_mode: str


class ElevenLabsVoiceService:
    """
    Canonical ElevenLabs voice gateway.

    Provider credentials remain server-side.
    Profile-specific voice identifiers are resolved only through PostgreSQL.
    """

    supported_content_types = {
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/wav",
        "audio/x-wav",
        "audio/aac",
        "audio/x-caf",
        "audio/ogg",
        "audio/opus",
        "application/octet-stream",
    }

    max_sample_bytes = int(
        os.getenv(
            "VOICE_CLONE_MAX_SAMPLE_BYTES",
            str(25 * 1024 * 1024),
        )
    )

    max_total_bytes = int(
        os.getenv(
            "VOICE_CLONE_MAX_TOTAL_BYTES",
            str(100 * 1024 * 1024),
        )
    )

    min_total_bytes = int(
        os.getenv(
            "VOICE_CLONE_MIN_TOTAL_BYTES",
            str(16 * 1024),
        )
    )

    max_samples = int(
        os.getenv(
            "VOICE_CLONE_MAX_SAMPLE_COUNT",
            "10",
        )
    )

    def __init__(
        self,
        repository: Optional[
            DigitalHumanProfileRepository
        ] = None,
    ) -> None:
        self.api_key = (
            os.getenv("ELEVENLABS_API_KEY")
            or ""
        ).strip()

        if not self.api_key:
            raise ElevenLabsVoiceProviderError(
                "ELEVENLABS_API_KEY is missing."
            )

        self.default_voice_id = (
            os.getenv(
                "ELEVENLABS_DEFAULT_VOICE_ID"
            )
            or ""
        ).strip()

        if not self.default_voice_id:
            raise ElevenLabsVoiceProviderError(
                "ELEVENLABS_DEFAULT_VOICE_ID is missing."
            )

        self.model_id = (
            os.getenv(
                "ELEVENLABS_MODEL_ID",
                "eleven_multilingual_v2",
            )
            or "eleven_multilingual_v2"
        ).strip()

        self.repository = (
            repository
            or DigitalHumanProfileRepository()
        )

    async def list_voices(self):
        async with httpx.AsyncClient(
            timeout=30
        ) as client:
            response = await client.get(
                "https://api.elevenlabs.io/v2/voices",
                params={"category": "premade"},
                headers={
                    "xi-api-key": self.api_key,
                },
            )

        self._raise_provider_error(
            response,
            operation="Voice listing",
        )

        payload = response.json()
        # The account also holds private profile clones. Never expose that catalog.
        public_fields = {"voice_id", "name", "category", "description", "preview_url", "labels"}
        voices = [{key: value for key, value in voice.items() if key in public_fields}
                  for voice in payload.get("voices", [])
                  if isinstance(voice, dict) and voice.get("category") == "premade"]
        return {"voices": voices}

    async def clone_voice(
        self,
        *,
        profile_id: UUID,
        display_name: str,
        samples: List[VoiceCloneSample],
        consent_verified: bool,
        remove_background_noise: bool,
        idempotency_key: str,
    ) -> VoiceCloneResult:
        clean_name = display_name.strip()
        clean_idempotency_key = (
            idempotency_key.strip()
        )

        if not consent_verified:
            raise ElevenLabsVoiceValidationError(
                "Voice cloning requires verified consent."
            )

        if not clean_name:
            raise ElevenLabsVoiceValidationError(
                "A display name is required."
            )

        if not clean_idempotency_key:
            raise ElevenLabsVoiceValidationError(
                "An idempotency key is required."
            )

        validated_samples = await asyncio.to_thread(self._validate_samples, samples)
        consent = await asyncio.to_thread(require_profile_purposes, profile_id, {"voice_synthesis"})
        await asyncio.to_thread(self._validate_sample_audio, validated_samples)

        profile = await asyncio.to_thread(self.repository.ensure,
            profile_id,
            consent_verified=True,
        )

        # A ready profile may be replacing its voice. Only the exact training
        # request may be reused, never the profile's previously trained voice.
        request_hash = await asyncio.to_thread(self._request_hash,
            profile_id=profile_id,
            idempotency_key=clean_idempotency_key,
            samples=validated_samples,
            remove_background_noise=remove_background_noise,
        )

        job_id = uuid4()

        job = await asyncio.to_thread(self.repository.create_training_job,
            job_id=job_id,
            profile_id=profile_id,
            training_type="voice",
            provider="elevenlabs",
            status="created",
            training_version=(
                profile.training_version
            ),
            idempotency_key=(
                f"voice:{profile_id}:"
                f"{request_hash}"
            ),
            request_payload={
                "sample_count":
                    len(validated_samples),
                "total_bytes":
                    sum(
                        len(sample.data)
                        for sample
                        in validated_samples
                    ),
                "remove_background_noise":
                    remove_background_noise,
            },
        )

        resolved_job_id = job["job_id"]

        if not bool(job.get("was_created", False)):
            if (
                UUID(str(job["profile_id"])) != profile_id
                or job["training_type"] != "voice"
                or job["provider"] != "elevenlabs"
            ):
                raise ElevenLabsVoiceConflictError(
                    "The idempotency key belongs to another training contract."
                )

            existing_voice_id = str(
                job.get("provider_job_id") or ""
            ).strip()
            existing_status = str(
                job.get("status") or "created"
            ).strip()

            if existing_voice_id:
                active = await asyncio.to_thread(self.repository.get, profile_id)
                if existing_status == "ready" and not (
                    active and active.has_personalized_voice
                    and active.voice_id == existing_voice_id
                    and active.voice_training_job_id == str(resolved_job_id)
                ):
                    raise ElevenLabsVoiceConflictError("This voice request is no longer the active selection.")
                return VoiceCloneResult(
                    job_id=resolved_job_id,
                    profile_id=profile_id,
                    voice_id=existing_voice_id,
                    status=existing_status,
                    requires_verification=(
                        existing_status == "verification_required"
                    ),
                )

            if existing_status == "failed":
                retry_was_claimed = (
                    await asyncio.to_thread(self.repository.restart_failed_voice_training_job,
                        job_id=resolved_job_id,
                        profile_id=profile_id,
                    )
                )

                if retry_was_claimed is None:
                    raise ElevenLabsVoiceConflictError(
                        "The previous voice request cannot be retried safely."
                    )

            elif existing_status != "created":
                raise ElevenLabsVoiceConflictError(
                    "The same voice training request is already being submitted."
                )

        claim = await asyncio.to_thread(
            self.repository.begin_voice_training,
            profile_id, resolved_job_id, consent.revision,
        )
        if not claim["submission_claimed"]:
            raise ElevenLabsVoiceConflictError("This voice request has already been submitted.")

        files = [
            (
                "files",
                (
                    sample.filename,
                    sample.data,
                    sample.content_type,
                ),
            )
            for sample in validated_samples
        ]

        form_data = {
            "name": (
                f"RememberMe-{clean_name[:48]}"
            ),
            "remove_background_noise": (
                "true"
                if remove_background_noise
                else "false"
            ),
        }

        voice_id = None
        try:
            await asyncio.to_thread(require_profile_purposes, profile_id, {"voice_synthesis"}, expected_revision=consent.revision)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=20,
                    read=180,
                    write=180,
                    pool=20,
                )
            ) as client:
                response = await client.post(
                    "https://api.elevenlabs.io/v1/voices/add",
                    headers={
                        "xi-api-key": self.api_key,
                    },
                    data=form_data,
                    files=files,
                )

            self._raise_provider_error(
                response,
                operation="Voice cloning",
            )

            voice_id, requires_verification = self._clone_response(response)

            profile_status = (
                "verification_required"
                if requires_verification
                else "ready"
            )

            await asyncio.to_thread(require_profile_purposes, profile_id, {"voice_synthesis"}, expected_revision=consent.revision)
            canonical = await asyncio.to_thread(
                self.repository.apply_voice_training_result,
                profile_id=profile_id,
                job_id=resolved_job_id,
                status=profile_status,
                voice_id=voice_id,
                provider_payload={
                    "voice_id": voice_id,
                    "requires_verification":
                        requires_verification,
                },
            )

            await asyncio.to_thread(require_profile_purposes, profile_id, {"voice_synthesis"}, expected_revision=consent.revision)
            if canonical["status"] != profile_status or (
                profile_status == "ready" and not canonical["voice_activated"]
            ):
                raise ElevenLabsVoiceConflictError("This voice result is no longer the current authorized selection.")

            return VoiceCloneResult(
                job_id=resolved_job_id,
                profile_id=profile_id,
                voice_id=voice_id,
                status=profile_status,
                requires_verification=(
                    requires_verification
                ),
            )

        except Exception as error:
            error_code = self._training_error_code(error)
            error_message = self._training_error_message(error)

            try:
                known_voice_id = voice_id or getattr(error, "created_voice_id", None)
                ambiguous = isinstance(error, httpx.HTTPError) or (
                    isinstance(error, ElevenLabsVoiceProviderError)
                    and (error.status_code is None or error.status_code in {408, 409} or error.status_code >= 500
                         or 300 <= error.status_code < 400)
                )
                await asyncio.to_thread(
                    self.repository.apply_voice_training_result,
                    profile_id=profile_id,
                    job_id=resolved_job_id,
                    status="submitted" if ambiguous else "failed",
                    voice_id=known_voice_id,
                    error_code=error_code,
                    error_message=error_message,
                )
            except (
                DigitalHumanProfileRepositoryError
            ):
                pass

            raise

    async def synthesize_for_profile(
        self,
        *,
        profile_id: UUID,
        text: str,
        delivery: VoiceDelivery | None = None,
    ) -> VoiceSynthesisResult:
        profile = await asyncio.to_thread(self.repository.get, profile_id)

        personalized_voice_id = self._personalized_voice_id(profile)
        purposes = {"voice_synthesis"} if personalized_voice_id else {"memory_context"}
        consent = require_profile_purposes(profile_id, purposes)

        if personalized_voice_id:
            try:
                result = VoiceSynthesisResult(
                    audio_stream=await self.synthesize(
                        text=text,
                        voice_id=personalized_voice_id,
                        delivery=delivery,
                    ),
                    voice_mode="personalized",
                )
                require_profile_purposes(profile_id, purposes, expected_revision=consent.revision)
                return result
            except ElevenLabsVoiceProviderError as error:
                if error.status_code in {404, 410}:
                    await asyncio.to_thread(self._mark_personalized_voice_unavailable,
                        profile_id=profile_id,
                        profile=profile,
                        error=error,
                    )

        require_profile_purposes(profile_id, purposes, expected_revision=consent.revision)
        result = VoiceSynthesisResult(
            audio_stream=await self.synthesize(
                text=text,
                voice_id=self.default_voice_id,
                delivery=delivery,
            ),
            voice_mode="warm_default",
        )
        require_profile_purposes(profile_id, purposes, expected_revision=consent.revision)
        return result

    async def synthesize(
        self,
        *,
        text: str,
        voice_id: Optional[str] = None,
        delivery: VoiceDelivery | None = None,
    ) -> BytesIO:
        clean_text = text.strip()

        if not clean_text:
            raise ElevenLabsVoiceValidationError(
                "Text is empty."
            )

        selected_voice_id = (
            voice_id
            or self.default_voice_id
        ).strip()

        payload = {
            "text": clean_text,
            "model_id": self.model_id,
            "voice_settings": self._voice_settings(delivery),
        }

        async with httpx.AsyncClient(
            timeout=90
        ) as client:
            response = await client.post(
                (
                    "https://api.elevenlabs.io/v1/"
                    f"text-to-speech/{selected_voice_id}"
                ),
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type":
                        "application/json",
                    "Accept": "audio/mpeg",
                },
                json=payload,
            )

        self._raise_provider_error(
            response,
            operation="Voice synthesis",
        )

        return BytesIO(response.content)

    def _voice_settings(self, delivery: VoiceDelivery | None) -> dict:
        if self.model_id.startswith("eleven_v3"):
            energy = delivery.energy if delivery else 0.5
            return {"stability": 0.0 if energy >= 0.75 else (1.0 if energy <= 0.25 else 0.5)}

        if delivery is None:
            return {
                "stability": 0.48, "similarity_boost": 0.82,
                "style": 0.22, "use_speaker_boost": True,
            }

        # Modulate delivery without changing the clone, pitch or accent.
        # Style exaggeration is disabled to avoid extra latency and artifacts.
        return {
            "stability": round(0.62 - 0.30 * delivery.energy, 3),
            "similarity_boost": 0.82,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": round(0.9 + 0.2 * delivery.speaking_speed
                           + 0.04 * (0.5 - delivery.pause_length), 3),
        }

    async def delete_profile_voice(
        self,
        *,
        profile_id: UUID,
    ) -> None:
        profile = self.repository.require(
            profile_id
        )

        voice_id = profile.voice_id

        if voice_id and profile.voice_provider != "elevenlabs":
            raise ElevenLabsVoiceError("The voice provider does not support this deletion path.")

        if not voice_id:
            self._clear_deleted_voice(profile)
            return

        await self.delete_voice_resource(voice_id=voice_id)
        self._clear_deleted_voice(profile)

    async def delete_voice_resource(self, *, voice_id: str) -> None:
        if not voice_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in voice_id):
            raise ElevenLabsVoiceValidationError("Invalid voice resource identifier.")
        async with httpx.AsyncClient(
            timeout=30
        ) as client:
            response = await client.delete(
                (
                    "https://api.elevenlabs.io/"
                    f"v1/voices/{voice_id}"
                ),
                headers={
                    "xi-api-key": self.api_key,
                },
            )

        if response.status_code not in {
            200,
            204,
            404,
        }:
            self._raise_provider_error(
                response,
                operation="Voice deletion",
            )

    def _clear_deleted_voice(self, profile: DigitalHumanProfile) -> None:
        try:
            self.repository.clear_voice_identity(
                profile.profile_id,
                expected_voice_id=profile.voice_id,
                expected_job_id=profile.voice_training_job_id,
                expected_provider=profile.voice_provider,
            )
        except StaleVoiceTrainingError as error:
            raise ElevenLabsVoiceConflictError(
                "The voice changed during deletion. Refresh before trying again."
            ) from error

    def status_for_profile(
        self,
        profile_id: UUID,
    ) -> dict:
        profile, latest_job = self.repository.get_voice_status_snapshot(profile_id)

        if profile is None:
            return {
                "profile_id": str(profile_id),
                "status": "not_started",
                "voice_mode": "warm_default",
                "voice_ready": False,
                "active_voice_version": None,
                "pending_training": None,
            }

        voice_ready = bool(profile["consent_verified"] and profile["voice_training_status"] == "ready"
                           and profile["voice_provider"] and profile["voice_id"])
        pending = None
        if latest_job and str(latest_job["job_id"]) != profile["voice_training_job_id"]:
            pending_status = latest_job["status"]
            if pending_status == "ready":
                pending_status = "superseded"
            pending = {
                "job_id": str(latest_job["job_id"]),
                "status": pending_status,
                "requires_verification": pending_status == "verification_required",
                "error_code": latest_job.get("error_code"),
                "error_message": latest_job.get("error_message"),
            }
        return {
            "profile_id":
                str(profile["profile_id"]),
            "status":
                profile["voice_training_status"],
            "voice_mode": (
                "personalized"
                if voice_ready
                else "warm_default"
            ),
            "voice_ready":
                voice_ready,
            "provider":
                profile["voice_provider"],
            "updated_at": profile.get("updated_at"),
            "active_voice_version": (profile["voice_training_job_id"] or profile.get("voice_ready_at")) if voice_ready else None,
            "pending_training": pending,
        }

    def _personalized_voice_id(
        self,
        profile: Optional[
            DigitalHumanProfile
        ],
    ) -> Optional[str]:
        if (
            profile is not None
            and profile.has_personalized_voice
            and profile.voice_provider
            == "elevenlabs"
            and profile.voice_id
        ):
            return profile.voice_id

        return None

    def _mark_personalized_voice_unavailable(
        self,
        *,
        profile_id: UUID,
        profile: Optional[DigitalHumanProfile],
        error: ElevenLabsVoiceProviderError,
    ) -> None:
        if profile is None:
            return

        try:
            self.repository.set_voice_training(
                profile_id,
                provider="elevenlabs",
                status="failed",
                provider_job_id=profile.voice_training_job_id,
                expected_job_id=profile.voice_training_job_id,
                expected_voice_id=profile.voice_id,
                error_code=type(error).__name__,
                error_message="The personalized voice is unavailable at the provider.",
            )
        except DigitalHumanProfileRepositoryError:
            # Generic synthesis remains truthful even when status persistence
            # is temporarily unavailable.
            pass

    def _validate_samples(
        self,
        samples: List[VoiceCloneSample],
    ) -> List[VoiceCloneSample]:
        if not samples:
            raise ElevenLabsVoiceValidationError(
                "At least one voice sample is required."
            )

        if len(samples) > self.max_samples:
            raise ElevenLabsVoiceValidationError(
                "Too many voice samples."
            )

        total_bytes = 0
        validated: List[
            VoiceCloneSample
        ] = []

        for sample in samples:
            filename = sample.filename.strip()
            content_type = (
                sample.content_type
                or "application/octet-stream"
            ).lower()

            if not filename:
                raise ElevenLabsVoiceValidationError(
                    "A voice sample has no filename."
                )

            if (
                content_type
                not in self.supported_content_types
            ):
                raise ElevenLabsVoiceValidationError(
                    "Unsupported voice sample type: "
                    f"{content_type}"
                )

            sample_size = len(sample.data)

            if sample_size < 16:
                raise ElevenLabsVoiceValidationError(
                    "A voice sample is too small."
                )

            if sample_size > self.max_sample_bytes:
                raise ElevenLabsVoiceValidationError(
                    "A voice sample exceeds the size limit."
                )

            total_bytes += sample_size
            validated.append(sample)

        if total_bytes < self.min_total_bytes:
            raise ElevenLabsVoiceValidationError(
                "Voice samples are too small."
            )

        if total_bytes > self.max_total_bytes:
            raise ElevenLabsVoiceValidationError(
                "Voice samples exceed the total size limit."
            )

        return validated

    @staticmethod
    def _validate_sample_audio(samples: List[VoiceCloneSample]) -> None:
        analyzer = AvatarMediaAnalysisService()
        for sample in samples:
            try:
                with NamedTemporaryFile(prefix="stay-voice-check-", suffix=".audio") as temporary:
                    temporary.write(sample.data)
                    temporary.flush()
                    analyzer.analyze(storage_path=temporary.name, asset_type="voice", content_type=sample.content_type)
            except AvatarMediaAnalysisUnavailableError:
                raise
            except AvatarMediaAnalysisError as error:
                raise ElevenLabsVoiceValidationError(str(error)) from error
            except OSError as error:
                raise AvatarMediaAnalysisUnavailableError("Voice sample validation is temporarily unavailable.") from error

    def _request_hash(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
        samples: List[VoiceCloneSample],
        remove_background_noise: bool = False,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(
            str(profile_id).encode(
                "utf-8"
            )
        )
        digest.update(
            idempotency_key.encode(
                "utf-8"
            )
        )

        for sample in samples:
            digest.update(
                sample.filename.encode(
                    "utf-8"
                )
            )
            digest.update(
                hashlib.sha256(
                    sample.data
                ).digest()
            )

        # Preserve existing default-operation keys while separating audio isolation.
        if remove_background_noise:
            digest.update(b"\x00remove_background_noise=true")

        return digest.hexdigest()

    def _raise_provider_error(
        self,
        response: httpx.Response,
        *,
        operation: str,
    ) -> None:
        if 200 <= response.status_code < 300:
            return

        raise ElevenLabsVoiceProviderError(
            f"{operation} failed "
            f"with provider status {response.status_code}.",
            status_code=response.status_code,
            provider_code=self._provider_error_code(response),
        )

    @staticmethod
    def _clone_response(response: httpx.Response) -> tuple[str, bool]:
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            raise ElevenLabsVoiceProviderError(
                "Voice creation returned an unverified response."
            ) from error
        if not isinstance(payload, dict):
            raise ElevenLabsVoiceProviderError(
                "Voice creation returned an unverified response."
            )
        voice_id = payload.get("voice_id")
        requires_verification = payload.get("requires_verification")
        if not isinstance(voice_id, str) or not voice_id.strip():
            raise ElevenLabsVoiceProviderError(
                "Voice creation returned no verified voice identity."
            )
        if not isinstance(requires_verification, bool):
            raise ElevenLabsVoiceProviderError(
                "Voice creation returned no verified verification state.",
                created_voice_id=voice_id.strip(),
            )
        return voice_id.strip(), requires_verification

    def _provider_error_code(
        self,
        response: httpx.Response,
    ) -> Optional[str]:
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return None

        candidates = []

        if isinstance(payload, dict):
            candidates.extend(
                payload.get(key)
                for key in ("code", "status", "type")
            )

            detail = payload.get("detail")
            if isinstance(detail, dict):
                candidates.extend(
                    detail.get(key)
                    for key in ("code", "status", "type")
                )

        for candidate in candidates:
            if not isinstance(candidate, str):
                continue

            normalized = "".join(
                character
                if character.isalnum() or character in {"_", "-"}
                else "_"
                for character in candidate.strip().lower()
            )[:64]

            if normalized:
                return normalized

        return None

    def _training_error_code(self, error: Exception) -> str:
        if (
            isinstance(error, ElevenLabsVoiceProviderError)
            and error.status_code is not None
        ):
            provider_code = error.provider_code or "unknown"
            return (
                f"provider_http_{error.status_code}_"
                f"{provider_code}"
            )[:120]

        if isinstance(error, httpx.TimeoutException):
            return "provider_transport_ambiguous_timeout"

        if isinstance(error, httpx.HTTPError):
            return "provider_transport_ambiguous"

        return "voice_training_internal_failure"

    def _training_error_message(self, error: Exception) -> str:
        if (
            isinstance(error, ElevenLabsVoiceProviderError)
            and error.is_capacity_unavailable
        ):
            return "Voice provider capacity is temporarily unavailable."

        if (
            isinstance(error, ElevenLabsVoiceProviderError)
            and error.status_code is not None
        ):
            return "The voice provider rejected the training request."

        if isinstance(error, httpx.HTTPError):
            return "Voice provider completion could not be confirmed."

        return "Voice training failed before completion."
