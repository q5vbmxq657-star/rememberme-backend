from __future__ import annotations

import asyncio
from io import BytesIO
from uuid import uuid4

from app.models.digital_human_profile import DigitalHumanProfile
from app.services.elevenlabs_voice_service import (
    ElevenLabsVoiceProviderError,
    ElevenLabsVoiceService,
)


class FakeRepository:
    def __init__(self, profile: DigitalHumanProfile | None):
        self.profile = profile
        self.voice_updates = []

    def get(self, profile_id):
        del profile_id
        return self.profile

    def set_voice_training(self, profile_id, **kwargs):
        self.voice_updates.append((profile_id, kwargs))
        return self.profile


def profile_with_personalized_voice() -> DigitalHumanProfile:
    return DigitalHumanProfile(
        profile_id=uuid4(),
        quality_tier="ready",
        quality_percentage=100,
        avatar_provider=None,
        avatar_replica_id=None,
        avatar_persona_id=None,
        avatar_training_job_id=None,
        avatar_training_status="not_started",
        voice_provider="elevenlabs",
        voice_id="personalized-id",
        voice_training_job_id="voice-job-id",
        voice_training_status="ready",
        approved_portrait_url=None,
        consent_verified=True,
        training_version=1,
        runtime_verified_at=None,
        avatar_ready_at=None,
        voice_ready_at=None,
        last_error_code=None,
        last_error_message=None,
    )


def service_with(repository: FakeRepository) -> ElevenLabsVoiceService:
    service = object.__new__(ElevenLabsVoiceService)
    service.repository = repository
    service.default_voice_id = "generic-id"
    return service


def test_personalized_synthesis_reports_personalized_mode():
    profile = profile_with_personalized_voice()
    repository = FakeRepository(profile)
    service = service_with(repository)

    async def synthesize(*, text, voice_id=None):
        assert text == "Hello"
        assert voice_id == "personalized-id"
        return BytesIO(b"personalized")

    service.synthesize = synthesize
    result = asyncio.run(
        service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello")
    )

    assert result.voice_mode == "personalized"
    assert result.audio_stream.read() == b"personalized"
    assert repository.voice_updates == []


def test_stale_personalized_voice_demotes_status_and_uses_truthful_generic_voice():
    profile = profile_with_personalized_voice()
    repository = FakeRepository(profile)
    service = service_with(repository)
    requested_voice_ids = []

    async def synthesize(*, text, voice_id=None):
        del text
        requested_voice_ids.append(voice_id)
        if voice_id == "personalized-id":
            raise ElevenLabsVoiceProviderError(
                "provider voice missing",
                status_code=404,
            )
        return BytesIO(b"generic")

    service.synthesize = synthesize
    result = asyncio.run(
        service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello")
    )

    assert requested_voice_ids == ["personalized-id", "generic-id"]
    assert result.voice_mode == "warm_default"
    assert result.audio_stream.read() == b"generic"
    assert repository.voice_updates[-1][1]["status"] == "failed"
    assert repository.voice_updates[-1][1]["provider_job_id"] == "voice-job-id"


def test_temporary_provider_failure_uses_generic_without_destroying_voice_identity():
    profile = profile_with_personalized_voice()
    repository = FakeRepository(profile)
    service = service_with(repository)

    async def synthesize(*, text, voice_id=None):
        del text
        if voice_id == "personalized-id":
            raise ElevenLabsVoiceProviderError(
                "provider unavailable",
                status_code=503,
            )
        return BytesIO(b"generic")

    service.synthesize = synthesize
    result = asyncio.run(
        service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello")
    )

    assert result.voice_mode == "warm_default"
    assert result.audio_stream.read() == b"generic"
    assert repository.voice_updates == []
