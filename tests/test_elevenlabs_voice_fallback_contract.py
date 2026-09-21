from __future__ import annotations

import asyncio
from io import BytesIO
from uuid import uuid4
import pytest
from fastapi import HTTPException
from unittest.mock import Mock, AsyncMock
from app.schemas.profile_consent import PurposeConsentSnapshot, CONSENT_POLICY_VERSION


@pytest.fixture
def valid_voice_consent(monkeypatch):
    def grant(profile_id, purposes, *, expected_revision=None):
        assert purposes in ({'voice_synthesis'}, {'memory_context'})
        assert expected_revision in (None, 7)
        return PurposeConsentSnapshot(profile_id=profile_id, revision=7,
            policy_version=CONSENT_POLICY_VERSION,
            purposes=sorted(purposes | {'provider_processing'}))
    guard = Mock(side_effect=grant)
    monkeypatch.setattr('app.services.elevenlabs_voice_service.require_profile_purposes', guard)
    return guard

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


def test_personalized_synthesis_reports_personalized_mode(valid_voice_consent):
    profile = profile_with_personalized_voice()
    repository = FakeRepository(profile)
    service = service_with(repository)

    async def synthesize(*, text, voice_id=None, delivery=None):
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


def test_stale_personalized_voice_demotes_status_and_uses_truthful_generic_voice(valid_voice_consent):
    profile = profile_with_personalized_voice()
    repository = FakeRepository(profile)
    service = service_with(repository)
    requested_voice_ids = []

    async def synthesize(*, text, voice_id=None, delivery=None):
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


@pytest.mark.parametrize("status", [400, 401, 403, 422, 429, 503])
def test_temporary_provider_failure_uses_generic_without_destroying_voice_identity(valid_voice_consent, status):
    profile = profile_with_personalized_voice()
    repository = FakeRepository(profile)
    service = service_with(repository)

    async def synthesize(*, text, voice_id=None, delivery=None):
        del text
        if voice_id == "personalized-id":
            raise ElevenLabsVoiceProviderError(
                "provider unavailable",
                status_code=status,
            )
        return BytesIO(b"generic")

    service.synthesize = synthesize
    result = asyncio.run(
        service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello")
    )

    assert result.voice_mode == "warm_default"
    assert result.audio_stream.read() == b"generic"
    assert repository.voice_updates == []


def test_denied_voice_grant_never_sends_text(valid_voice_consent):
    profile = profile_with_personalized_voice()
    service = service_with(FakeRepository(profile))
    service.synthesize = AsyncMock()
    valid_voice_consent.side_effect = HTTPException(403, 'Permission required')
    with pytest.raises(HTTPException):
        asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text='Private'))
    service.synthesize.assert_not_called()


@pytest.mark.parametrize('status', [404, 503])
def test_revoked_grant_prevents_generic_fallback(valid_voice_consent, status):
    profile = profile_with_personalized_voice()
    service = service_with(FakeRepository(profile))
    async def fail(**kwargs):
        valid_voice_consent.side_effect = HTTPException(409, 'Permissions changed')
        raise ElevenLabsVoiceProviderError('Unavailable', status_code=status)
    service.synthesize = AsyncMock(side_effect=fail)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text='Private'))
    assert caught.value.status_code == 409
    assert service.synthesize.await_count == 1
    assert service.synthesize.call_args.kwargs['voice_id'] == 'personalized-id'
