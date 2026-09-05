from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx

from app.models.digital_human_profile import DigitalHumanProfile
from app.services.elevenlabs_voice_service import (
    ElevenLabsVoiceConflictError,
    ElevenLabsVoiceProviderError,
    ElevenLabsVoiceService,
    VoiceCloneSample,
)


def profile_without_voice() -> DigitalHumanProfile:
    return DigitalHumanProfile(
        profile_id=uuid4(),
        quality_tier="premium_presence",
        quality_percentage=25,
        avatar_provider=None,
        avatar_replica_id=None,
        avatar_persona_id=None,
        avatar_training_job_id=None,
        avatar_training_status="not_started",
        voice_provider=None,
        voice_id=None,
        voice_training_job_id=None,
        voice_training_status="failed",
        approved_portrait_url=None,
        consent_verified=True,
        training_version=1,
        runtime_verified_at=None,
        avatar_ready_at=None,
        voice_ready_at=None,
        last_error_code=None,
        last_error_message=None,
    )


class CloneRepository:
    def __init__(self, *, retryable: bool) -> None:
        self.profile = profile_without_voice()
        self.job_id = uuid4()
        self.retryable = retryable
        self.restart_count = 0
        self.voice_updates = []
        self.job_updates = []

    def ensure(self, profile_id, *, consent_verified):
        assert profile_id == self.profile.profile_id
        assert consent_verified is True
        return self.profile

    def create_training_job(self, **kwargs):
        assert kwargs["profile_id"] == self.profile.profile_id
        return {
            "job_id": self.job_id,
            "profile_id": self.profile.profile_id,
            "training_type": "voice",
            "provider": "elevenlabs",
            "provider_job_id": None,
            "status": "failed",
            "was_created": False,
        }

    def restart_failed_voice_training_job(self, *, job_id, profile_id):
        self.restart_count += 1
        assert job_id == self.job_id
        assert profile_id == self.profile.profile_id
        if not self.retryable:
            return None
        return {
            "job_id": self.job_id,
            "profile_id": self.profile.profile_id,
            "training_type": "voice",
            "provider": "elevenlabs",
            "provider_job_id": None,
            "status": "created",
        }

    def set_voice_training(self, profile_id, **kwargs):
        self.voice_updates.append((profile_id, kwargs))
        return self.profile

    def update_training_job(self, job_id, **kwargs):
        self.job_updates.append((job_id, kwargs))
        return {"job_id": job_id, **kwargs}


class ProviderResponse:
    status_code = 200
    content = b""

    def json(self):
        return {
            "voice_id": "voice-created-once",
            "requires_verification": False,
        }


class ProviderClient:
    post_count = 0

    def __init__(self, *args, **kwargs):
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback

    async def post(self, *args, **kwargs):
        del args, kwargs
        type(self).post_count += 1
        return ProviderResponse()


def clone_service(repository: CloneRepository) -> ElevenLabsVoiceService:
    service = object.__new__(ElevenLabsVoiceService)
    service.repository = repository
    service.api_key = "test-key"
    service.default_voice_id = "default-voice"
    service.max_samples = 10
    service.max_sample_bytes = 25 * 1024 * 1024
    service.max_total_bytes = 100 * 1024 * 1024
    service.min_total_bytes = 16
    return service


def test_provider_rejected_job_is_reclaimed_and_submitted_once(monkeypatch):
    repository = CloneRepository(retryable=True)
    service = clone_service(repository)
    ProviderClient.post_count = 0
    monkeypatch.setattr(httpx, "AsyncClient", ProviderClient)

    result = asyncio.run(
        service.clone_voice(
            profile_id=repository.profile.profile_id,
            display_name="Anna",
            samples=[
                VoiceCloneSample(
                    filename="voice.m4a",
                    content_type="audio/m4a",
                    data=b"clear-voice-sample",
                )
            ],
            consent_verified=True,
            remove_background_noise=False,
            idempotency_key="profile-bound-asset-id",
        )
    )

    assert repository.restart_count == 1
    assert ProviderClient.post_count == 1
    assert result.profile_id == repository.profile.profile_id
    assert result.voice_id == "voice-created-once"
    assert result.status == "ready"
    assert repository.job_updates[-1][1]["status"] == "ready"
    assert repository.voice_updates[-1][1]["status"] == "ready"


def test_ambiguous_failed_job_is_not_resubmitted(monkeypatch):
    repository = CloneRepository(retryable=False)
    service = clone_service(repository)
    ProviderClient.post_count = 0
    monkeypatch.setattr(httpx, "AsyncClient", ProviderClient)

    try:
        asyncio.run(
            service.clone_voice(
                profile_id=repository.profile.profile_id,
                display_name="Anna",
                samples=[
                    VoiceCloneSample(
                        filename="voice.m4a",
                        content_type="audio/m4a",
                        data=b"clear-voice-sample",
                    )
                ],
                consent_verified=True,
                remove_background_noise=False,
                idempotency_key="profile-bound-asset-id",
            )
        )
    except ElevenLabsVoiceConflictError:
        pass
    else:
        raise AssertionError("Ambiguous provider work must not be duplicated.")

    assert ProviderClient.post_count == 0


def test_provider_capacity_code_is_safely_classified():
    service = object.__new__(ElevenLabsVoiceService)
    request = httpx.Request("POST", "https://api.elevenlabs.io/v1/voices/add")
    response = httpx.Response(
        400,
        request=request,
        json={
            "detail": {
                "status": "voice_limit_reached",
                "message": "provider text must not be persisted",
            }
        },
    )

    try:
        service._raise_provider_error(response, operation="Voice cloning")
    except ElevenLabsVoiceProviderError as error:
        assert error.provider_code == "voice_limit_reached"
        assert error.is_capacity_unavailable is True
        assert (
            service._training_error_code(error)
            == "provider_http_400_voice_limit_reached"
        )
        assert "provider text" not in service._training_error_message(error)
    else:
        raise AssertionError("Provider failure was not raised.")


def test_voice_verification_state_is_supported_by_database_contract():
    migration = Path(
        "migrations/016_voice_verification_status.sql"
    ).read_text(encoding="utf-8")

    assert "verification_required" in migration
    assert "digital_human_profiles_voice_status_check" in migration
    assert "digital_human_training_jobs_status_check" in migration
