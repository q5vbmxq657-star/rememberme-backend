from __future__ import annotations

import hashlib
import os

from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional
from uuid import UUID, uuid4

import httpx

from app.models.digital_human_profile import (
    DigitalHumanProfile,
)
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileNotFoundError,
    DigitalHumanProfileRepository,
    DigitalHumanProfileRepositoryError,
)


class ElevenLabsVoiceError(RuntimeError):
    pass


class ElevenLabsVoiceValidationError(
    ElevenLabsVoiceError
):
    pass


class ElevenLabsVoiceProviderError(
    ElevenLabsVoiceError
):
    pass


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
                headers={
                    "xi-api-key": self.api_key,
                },
            )

        self._raise_provider_error(
            response,
            operation="Voice listing",
        )

        return response.json()

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

        validated_samples = (
            self._validate_samples(samples)
        )

        profile = self.repository.ensure(
            profile_id,
            consent_verified=True,
        )

        if (
            profile.voice_training_status
            == "ready"
            and profile.voice_provider
            == "elevenlabs"
            and profile.voice_id
        ):
            return VoiceCloneResult(
                job_id=uuid4(),
                profile_id=profile.profile_id,
                voice_id=profile.voice_id,
                status="ready",
                requires_verification=False,
            )

        request_hash = self._request_hash(
            profile_id=profile_id,
            idempotency_key=clean_idempotency_key,
            samples=validated_samples,
        )

        job_id = uuid4()

        job = self.repository.create_training_job(
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

        self.repository.set_voice_training(
            profile_id,
            provider="elevenlabs",
            status="submitted",
            provider_job_id=str(
                resolved_job_id
            ),
        )

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

        try:
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

            payload = response.json()
            voice_id = str(
                payload.get("voice_id")
                or ""
            ).strip()

            if not voice_id:
                raise ElevenLabsVoiceProviderError(
                    "Voice cloning response did not include voice_id."
                )

            requires_verification = bool(
                payload.get(
                    "requires_verification",
                    False,
                )
            )

            profile_status = (
                "training"
                if requires_verification
                else "ready"
            )

            self.repository.update_training_job(
                resolved_job_id,
                status=profile_status,
                provider_job_id=voice_id,
                provider_payload={
                    "voice_id": voice_id,
                    "requires_verification":
                        requires_verification,
                },
            )

            self.repository.set_voice_training(
                profile_id,
                provider="elevenlabs",
                status=profile_status,
                provider_job_id=str(
                    resolved_job_id
                ),
                voice_id=voice_id,
            )

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
            error_message = str(error)[:1000]

            try:
                self.repository.update_training_job(
                    resolved_job_id,
                    status="failed",
                    error_code=(
                        type(error).__name__
                    ),
                    error_message=error_message,
                )

                self.repository.set_voice_training(
                    profile_id,
                    provider="elevenlabs",
                    status="failed",
                    provider_job_id=str(
                        resolved_job_id
                    ),
                    error_code=(
                        type(error).__name__
                    ),
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
    ) -> BytesIO:
        profile = self.repository.get(
            profile_id
        )

        voice_id = self._resolve_voice_id(
            profile
        )

        return await self.synthesize(
            text=text,
            voice_id=voice_id,
        )

    async def synthesize(
        self,
        *,
        text: str,
        voice_id: Optional[str] = None,
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
            "voice_settings": {
                "stability": 0.48,
                "similarity_boost": 0.82,
                "style": 0.22,
                "use_speaker_boost": True,
            },
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

    async def delete_profile_voice(
        self,
        *,
        profile_id: UUID,
    ) -> None:
        profile = self.repository.require(
            profile_id
        )

        voice_id = profile.voice_id

        if (
            profile.voice_provider
            != "elevenlabs"
            or not voice_id
        ):
            self.repository.set_voice_training(
                profile_id,
                provider="elevenlabs",
                status="deleted",
                provider_job_id=None,
                voice_id=None,
            )
            return

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

        self.repository.clear_voice_identity(
            profile_id
        )

    def status_for_profile(
        self,
        profile_id: UUID,
    ) -> dict:
        profile = self.repository.get(
            profile_id
        )

        if profile is None:
            return {
                "profile_id": str(profile_id),
                "status": "not_started",
                "voice_mode": "warm_default",
                "voice_ready": False,
            }

        return {
            "profile_id":
                str(profile.profile_id),
            "status":
                profile.voice_training_status,
            "voice_mode": (
                "personalized"
                if profile.has_personalized_voice
                else "warm_default"
            ),
            "voice_ready":
                profile.has_personalized_voice,
            "provider":
                profile.voice_provider,
            "updated_at": (
                profile.updated_at.isoformat()
                if profile.updated_at
                else None
            ),
        }

    def _resolve_voice_id(
        self,
        profile: Optional[
            DigitalHumanProfile
        ],
    ) -> str:
        if (
            profile is not None
            and profile.has_personalized_voice
            and profile.voice_provider
            == "elevenlabs"
            and profile.voice_id
        ):
            return profile.voice_id

        return self.default_voice_id

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

    def _request_hash(
        self,
        *,
        profile_id: UUID,
        idempotency_key: str,
        samples: List[VoiceCloneSample],
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

        return digest.hexdigest()

    def _raise_provider_error(
        self,
        response: httpx.Response,
        *,
        operation: str,
    ) -> None:
        if response.status_code < 400:
            return

        detail = (
            response.text
            or "Unknown provider error."
        )[:1000]

        raise ElevenLabsVoiceProviderError(
            f"{operation} failed "
            f"({response.status_code}): {detail}"
        )
